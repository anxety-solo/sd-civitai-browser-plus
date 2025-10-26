import urllib.parse
import requests
import platform
import json
import os
import re
import gradio as gr
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
from html import escape
from io import BytesIO
from PIL import Image

# === WebUI imports ===
from modules.paths import models_path, extensions_dir, data_path
from modules.images import read_info_from_image
from modules.shared import cmd_opts, opts

# === Extension imports ===
import scripts.civitai_download as _download
import scripts.civitai_file_manage as _file
import scripts.civitai_global as gl
from scripts.civitai_global import print, debug_print


gl.init()


## === ANXETY EDITs ===
# Mapping for short/clear display names for model types
MODEL_TYPE_DISPLAY_NAMES = {
    'TextualInversion': 'Embedding',
    'AestheticGradient': 'Aesthetic',
    'MotionModule': 'Motion',
    'Workflows': 'Workflow',
    'Wildcards': 'Wildcard'
}

def get_display_type(type_name):
    """Return short/clear display name for model type"""
    return MODEL_TYPE_DISPLAY_NAMES.get(type_name, type_name)

def is_early_access(version_data):
    """Check if the model is an early access"""
    avail = version_data.get('availability')
    return isinstance(avail, str) and avail == 'EarlyAccess'

# This nsfwlevel system is not accurate...
def is_model_nsfw(model_data, nsfw_level=8):
    """Determine if a model is NSFW based on its metadata and first image"""
    if model_data.get('nsfw'):
        return True
    model_versions = model_data.get('modelVersions')
    if model_versions and model_versions[0].get('images'):
        first_image = model_versions[0]['images'][0]
        if first_image.get('nsfwLevel', 0) >= nsfw_level:
            return True
    return False

def normalize_sha256(sha256_hash):
    """Normalize SHA256 hash to uppercase and validate format"""
    if not sha256_hash:
        return None
    return sha256_hash.strip().upper()

def safe_json_load(file_path):
    """Safely load JSON file with error handling"""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading JSON from {file_path}: {e}")
        return None

def safe_json_save(file_path, data):
    """Safely save JSON file with error handling"""
    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error saving JSON to {file_path}: {e}")
        return False


def contenttype_folder(content_type, desc=None, custom_folder=None):
    """
    Returns the appropriate folder path for a given content type.
    Args:
        content_type (str): The type of content/model.
        desc (str, optional): Description or additional info for type-specific logic.
        custom_folder (str or Path, optional): Custom base folder to use instead of defaults.
    Returns:
        Path: The resolved folder path for the content type, or None if not found.
    """
    desc_upper  = (desc or 'PLACEHOLDER').upper()                               # Uppercase description for type checks
    main_models = Path(custom_folder) if custom_folder else Path(models_path)   # Main models folder path
    main_data   = Path(custom_folder) if custom_folder else Path(data_path)     # Main data folder path (WebUI root)
    ext_dir     = Path(extensions_dir)                                          # Extensions directory path

    def resolve_path(attr, fallback):
        # Returns a Path from cmd_opts if set, otherwise fallback
        if getattr(cmd_opts, attr, None) and not custom_folder:
            return Path(getattr(cmd_opts, attr))
        return fallback

    # Mapping for content types
    content_type_map = {
        'modelFolder': lambda: main_models,
        'Checkpoint': lambda: resolve_path('ckpt_dir', main_models / 'Stable-diffusion'),
        'TextualInversion': lambda: resolve_path('embeddings_dir', main_data / 'embeddings'),
        'AestheticGradient': lambda: (Path(custom_folder) if custom_folder else ext_dir / 'stable-diffusion-webui-aesthetic-gradients') / 'aesthetic_embeddings',
        'LORA': lambda: resolve_path('lora_dir', main_models / 'Lora'),
        'VAE': lambda: resolve_path('vae_dir', main_models / 'VAE'),
        'Controlnet': lambda: resolve_path('controlnet_dir', main_models / 'ControlNet'),
        'Poses': lambda: main_models / 'Poses',
        'MotionModule': lambda: ext_dir / 'sd-webui-animatediff' / 'model',
        'Workflows': lambda: main_models / 'Workflows',
        'Other': lambda: main_models / 'adetailer' if 'ADETAILER' in desc_upper else main_models / 'Other',
        'Wildcards': lambda: (ext_dir / 'UnivAICharGen' / 'wildcards') if (ext_dir / 'UnivAICharGen' / 'wildcards').exists() else (ext_dir / 'sd-dynamic-prompts' / 'wildcards'),
        'Upscaler': lambda: _resolve_upscaler_folder(desc_upper, main_models, resolve_path)
    }

    def _resolve_upscaler_folder(desc, main_models, resolve_path):
        # Helper for upscaler folder logic
        if 'SWINIR' in desc:
            return resolve_path('swinir_models_path', main_models / 'SwinIR')
        if 'REALESRGAN' in desc:
            return resolve_path('realesrgan_models_path', main_models / 'RealESRGAN')
        if 'GFPGAN' in desc:
            return resolve_path('gfpgan_models_path', main_models / 'GFPGAN')
        if 'BSRGAN' in desc:
            return resolve_path('bsrgan_models_path', main_models / 'BSRGAN')
        return resolve_path('esrgan_models_path', main_models / 'ESRGAN')

    # Get the folder resolver function for the content type
    folder_resolver = content_type_map.get(content_type)
    if folder_resolver:
        return folder_resolver()

    return None

def model_list_html(json_data):
    def filter_versions(item, hide_early_access, current_time):
        """Filter model versions based on file presence and early access status"""
        versions = []
        for version in item.get('modelVersions', []):
            if not version.get('files'):
                continue
            if hide_early_access and is_early_access(version):
                continue
            versions.append(version)
        return versions

    def collect_existing_files(model_folders):
        """Collect existing file names and SHA256 hashes from model folders"""
        files_set = set()
        sha256_set = set()
        for folder in model_folders:
            for root, _, files in os.walk(folder, followlinks=True):
                for file in files:
                    files_set.add(file.lower())
                    if file.endswith('.json'):
                        json_path = os.path.join(root, file)
                        json_data = safe_json_load(json_path)
                        if json_data and isinstance(json_data, dict):
                            sha256 = normalize_sha256(json_data.get('sha256'))
                            if sha256:
                                sha256_set.add(sha256)
        return files_set, sha256_set

    ## === ANXETY EDITs ===
    def get_model_card(item, existing_files, existing_files_sha256, playback):
        """Build HTML for a single model card (civmodelcard - Browser Card)"""
        model_id = item.get('id')
        model_name = item.get('name', '')
        is_nsfw = is_model_nsfw(item)
        nsfw_class = 'civcardnsfw' if is_nsfw else ''

        # Find the first installed version or fallback to the first version
        display_version = None
        for version in item.get('modelVersions', []):
            for file in version.get('files', []):
                file_name = file['name']
                file_sha256 = normalize_sha256(file.get('hashes', {}).get('SHA256', ''))
                name_match = file_name.lower() in existing_files
                sha256_match = file_sha256 and file_sha256 in existing_files_sha256
                if name_match or sha256_match:
                    display_version = version
                    break
            if display_version:
                break

        # Fallback to first version if no installed version found
        if not display_version and item['modelVersions']:
            display_version = item['modelVersions'][0]

        base_model = display_version.get('baseModel', 'Not Found') if display_version else 'Not Found'
        date = display_version.get('publishedAt', 'Not Found').split('T')[0] if display_version and 'publishedAt' in display_version else 'Not Found'

        early_access = is_early_access(display_version) if display_version else False
        early_access_class = 'early-access' if early_access else ''

        # Image or video preview
        images = display_version.get('images', []) if display_version else []
        if images:
            media_type = images[0].get('type')
            image_url = images[0].get('url')

            # Apply resize if enabled
            resize_preview = getattr(opts, 'resize_preview_cards', True)
            resize_size = getattr(opts, 'resize_preview_size', 512)

            if resize_preview and media_type == 'image':
                # For images, modify the URL to request specific size
                image_url = re.sub(r'/width=\d+', f"/width={resize_size}", image_url)

            if media_type == 'video':
                if resize_preview:
                    # For videos, replace or add width parameter
                    if '/width=' in image_url:
                        image_url = re.sub(r'/width=\d+', f"/width={resize_size}", image_url)
                    else:
                        image_url = image_url.replace('transcode=true,', f"transcode=true,width={resize_size},")
                else:
                    image_url = image_url.replace('width=', 'transcode=true,width=')
                imgtag = f'<video class="video-bg" {playback} muted playsinline><source src="{image_url}" type="video/mp4"></video>'
            else:
                imgtag = f'<img src="{image_url}"></img>'
        else:
            # Try PNG first, then fallback to JPEG if PNG does not exist
            imgtag = '<img src="./file=html/card-no-preview.png" onerror="this.onerror=null;this.src=\'./file=html/card-no-preview.jpg\';"></img>'

        # Install status - check if model is installed and determine if it's outdated
        ## Note: Sensitive check for updates by `name_match`... (It is possible that an outdated version of the model will not be marked as outdated)
        installstatus = ''
        model_versions = item.get('modelVersions', [])
        if model_versions:
            precise_check = getattr(opts, 'precise_version_check', True)
            installed_map, available_map = {}, {}  # family → list of version parts
            installed_all, available_all = [], []  # all versions (no family grouping)

            # --- Collect version and installation info ---
            for version in model_versions:
                version_name = version.get('name', '')
                family, version_parts = _file.extract_version_from_ver_name(version_name)

                if precise_check and family:
                    available_map.setdefault(family, []).append(version_parts)
                else:
                    available_all.append(version_parts)

                # Check if any file of this version is installed
                for file in version.get('files', []):
                    file_name = file['name'].lower()
                    file_sha256 = normalize_sha256(file.get('hashes', {}).get('SHA256', ''))
                    name_match = file_name in existing_files
                    sha_match = file_sha256 and file_sha256 in existing_files_sha256

                    if sha_match or name_match:
                        if precise_check and family:
                            installed_map.setdefault(family, []).append(version_parts)
                        else:
                            installed_all.append(version_parts)
                        break

            # Check installed
            has_installed = bool(installed_map or installed_all)
            if has_installed:
                has_outdated = False

                def is_outdated(inst, avail):
                    """Compare max installed and available versions"""
                    max_inst = max(inst, key=lambda x: x or [0])
                    max_avail = max(avail, key=lambda x: x or [0])
                    cmp = _file.compare_version_parts(max_inst, max_avail)
                    return cmp < 0

                # Comparison by families
                if precise_check and available_map:
                    for fam, avail in available_map.items():
                        inst = installed_map.get(fam)
                        if inst and is_outdated(inst, avail):
                            has_outdated = True
                            break
                # Comparison without families
                elif installed_all and available_all:
                    has_outdated = is_outdated(installed_all, available_all)

                if has_outdated:
                    installstatus = 'civmodelcardoutdated'
                else:
                    installstatus = 'civmodelcardinstalled'

        # Model name for JS and HTML
        model_name_js = model_name.replace("'", "\\'")
        model_string = escape(f"{model_name_js} ({model_id})")
        display_name = escape(model_name[:35] + '...' if len(model_name) > 35 else model_name)
        full_name = escape(model_name)

        ## Badges
        # Model Type Badge ( + Early Access)
        if early_access:
            # Gold badge with a lightning icon
            model_type_badge = (
                f'<div class="model-type-badge {item["type"].lower()} early-access-badge">'
                '<svg class="early-access-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="currentColor">'
                '<path d="M13 2L3 14h9l-1 8 10-12h-8z"/>'
                '</svg>'
                f'{get_display_type(item["type"])}'
                '</div>'
            )
        else:
            model_type_badge = f'<div class="model-type-badge {item["type"].lower()}">{get_display_type(item["type"])}</div>'

        # NSFW Badge - only show for nsfw cards and if setting is enabled
        show_nsfw_badge = getattr(opts, 'show_nsfw_badge', True)
        if is_nsfw and show_nsfw_badge:
            nsfw_badge = (
                '<div class="nsfw-badge">'
                '<svg class="nsfw-badge-icon" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg" fill="currentColor">'
                '<circle cx="10" cy="10" r="10"/>'
                '<text x="10" y="11" font-size="12" text-anchor="middle" dominant-baseline="middle" font-family="Arial" font-weight="bold" fill="#fff">!</text>'
                '</svg>'
                'NSFW'
                '</div>'
            )
        else:
            nsfw_badge = ''

        # ModelCard HTML (Header)
        card_html = (
            f'<figure class="civmodelcard {nsfw_class} {early_access_class} {installstatus}" base-model="{base_model}" date="{date}" '
            f'onclick="select_model(\'{model_string}\', event)">'
            f'<div class="card-header">'
            f'<div class="badges-container">{model_type_badge}{nsfw_badge}</div>'
        )

        if installstatus != 'civmodelcardinstalled':
            card_html += (
                f'<div class="checkbox-container">'
                f'<input type="checkbox" class="model-checkbox" id="checkbox-{model_string}" '
                f'onchange="multi_model_select(\'{model_string}\', \'{item["type"]}\', this.checked)">'
                f'<label for="checkbox-{model_string}" class="custom-checkbox">'
                f'<span class="checkbox-checkmark"></span>'
                f'</label>'
                f'</div>'
            )

        # ModelCard HTML (Footer)
        card_html += (
            f'</div>'
            f'{imgtag}'
            f'<figcaption title="{full_name}">{display_name}</figcaption></figure>'
        )
        return card_html, date

    # Main function logic
    video_playback = getattr(opts, 'video_playback', True)
    playback = 'autoplay loop' if video_playback else ''
    hide_early_access = getattr(opts, 'hide_early_access', True)
    current_time = datetime.now(timezone.utc)

    # Filter model versions and items
    filtered_items = []
    for item in json_data.get('items', []):
        versions = filter_versions(item, hide_early_access, current_time)
        if versions:
            item['modelVersions'] = versions
            filtered_items.append(item)
    json_data['items'] = filtered_items

    # Collect model folders
    model_folders = {
        os.path.join(contenttype_folder(item['type'], item['description']))
        for item in json_data['items']
    }
    existing_files, existing_files_sha256 = collect_existing_files(model_folders)

    # Build HTML
    HTML = '<div class="column civmodellist">'
    sorted_models = {} if gl.sortNewest else None

    for item in json_data['items']:
        model_card, date = get_model_card(item, existing_files, existing_files_sha256, playback)
        if gl.sortNewest:
            if date not in sorted_models:
                sorted_models[date] = []
            sorted_models[date].append(model_card)
        else:
            HTML += model_card

    if gl.sortNewest:
        HTML += '<div class="date-sections-container">'
        for date, cards in sorted(sorted_models.items(), reverse=True):
            if not cards:
                continue

            if date == 'Not Found':
                formatted_date = 'Unknown Date'
            else:
                try:
                    date_obj = datetime.strptime(date, '%Y-%m-%d')
                    formatted_date = date_obj.strftime('%B %d, %Y')
                except:
                    formatted_date = date  # Fallback to original format

            # Add card counter (only show if more than 1 card)
            card_count = len(cards)
            counter_html = f' <span class="card-counter">{card_count}</span>' if card_count > 1 else ''
            HTML += (
                f'<div class="date-section">'
                f'<h4>{formatted_date}{counter_html}</h4>'
                '<div class="card-row">'
            )
            for card in cards:
                HTML += card
            HTML += '</div></div>'
        HTML += '</div>'
    HTML += '</div>'

    return HTML

def _search_by_sha256(sha256_hash):
    """Search for a model by SHA256 hash"""
    # Normalize and validate hash format
    normalized_hash = normalize_sha256(sha256_hash)
    if not normalized_hash or not re.match(r'^[A-F0-9]{64}$', normalized_hash):
        return 'invalid_hash'

    # Search for model version by hash
    api_url = f"https://civitai.com/api/v1/model-versions/by-hash/{normalized_hash}"
    headers = get_headers()
    proxies, ssl = get_proxies()

    try:
        response = requests.get(api_url, headers=headers, timeout=(60, 30), proxies=proxies, verify=ssl)

        if response.status_code == 200:
            data = response.json()
            if 'error' in data:
                return 'sha256_not_found'

            # Get model ID and fetch full model data
            model_id = data.get('modelId')
            if not model_id:
                return 'not_found'

            model_url = f"https://civitai.com/api/v1/models/{model_id}"
            model_response = requests.get(model_url, headers=headers, timeout=(60, 30), proxies=proxies, verify=ssl)

            if model_response.status_code == 200:
                model_data = model_response.json()
                return {
                    'items': [model_data],
                    'metadata': {
                        'totalItems': 1,
                        'currentPage': 1,
                        'pageSize': 1,
                        'totalPages': 1
                    }
                }
            return 'not_found'

        elif response.status_code == 404:
            return 'sha256_not_found'
        elif response.status_code == 503:
            return 'offline'
        else:
            return 'error'

    except requests.exceptions.Timeout:
        return 'timeout'
    except (requests.exceptions.RequestException, Exception):
        return 'error'

def create_api_url(content_type=None, sort_type=None, period_type=None, use_search_term=None, base_filter=None, only_liked=None, tile_count=None, search_term=None, nsfw=None, exact_search=None, isNext=None):
    base_url = 'https://civitai.com/api/v1/models'
    version_url = 'https://civitai.com/api/v1/model-versions'

    if isNext != None:
        api_url = gl.json_data['metadata']['nextPage' if isNext else 'prevPage']
        debug_print(api_url)
        return api_url

    params = {'limit': tile_count, 'sort': sort_type, 'period': period_type.replace(' ', '') if period_type else None}

    if content_type:
        params['types'] = content_type

    ## === ANXETY EDITs ===
    if use_search_term != 'None' and search_term:
        search_term = search_term.replace('\\', '\\\\').lower()

        # Apply exact search logic - wrap search term in quotes if exact_search is True
        if exact_search and use_search_term in ['Model name', 'User name', 'Tag']:
            # Only wrap in quotes if not already wrapped and contains spaces
            if not (search_term.startswith('"') and search_term.endswith('"')) and ' ' in search_term:
                search_term = f'"{search_term}"'

        if 'civitai.com' in search_term:
            if '/api/download/models' in search_term:
                # Extract version ID from download URL
                version_match = re.search(r'models/(\d+)', search_term)
                if version_match:
                    version_id = version_match.group(1)
                    # Make API request to get model version information
                    version_api_url = f"https://civitai.com/api/v1/model-versions/{version_id}"
                    version_data = request_civit_api(version_api_url, skip_error_check=True)

                    if isinstance(version_data, dict) and 'modelId' in version_data:
                        model_id = version_data['modelId']
                        params = {'ids': str(model_id)}
            else:
                model_match = re.search(r'models/(\d+)', search_term)
                if model_match:
                    model_number = model_match.group(1)
                    params = {'ids': model_number}
        elif use_search_term == 'SHA256':
            # SHA256 search is handled separately in initial_model_page
            pass
        else:
            key_map = {'User name': 'username', 'Tag': 'tag'}
            search_key = key_map.get(use_search_term, 'query')
            params[search_key] = search_term

    if base_filter:
        params['baseModels'] = base_filter

    if only_liked:
        params['favorites'] = 'true'

    params['nsfw'] = 'true' if nsfw else 'false'

    query_parts = []
    for key, value in params.items():
        if isinstance(value, list):
            for item in value:
                query_parts.append((key, item))
        else:
            query_parts.append((key, value))

    query_string = urllib.parse.urlencode(query_parts, doseq=True, quote_via=urllib.parse.quote)
    api_url = f"{base_url}?{query_string}"

    debug_print(api_url)
    return api_url


## === ANXETY EDITs ===
def initial_model_page(content_type=None, sort_type=None, period_type=None, use_search_term=None, search_term=None, current_page=None, base_filter=None, only_liked=None, nsfw=None, exact_search=None, tile_count=None, from_update_tab=False):
    current_inputs = (content_type, sort_type, period_type, use_search_term, search_term, tile_count, base_filter, nsfw, exact_search)
    if current_inputs != gl.previous_inputs and gl.previous_inputs != None or not current_page:
        current_page = 1
    gl.previous_inputs = current_inputs

    if not from_update_tab:
        gl.from_update_tab = False

        if current_page == 1:
            # Handle SHA256 search specially
            if use_search_term == 'SHA256' and search_term:
                debug_print(f"Performing SHA256 search for hash: {search_term}")
                gl.json_data = _search_by_sha256(search_term)
                gl.url_list = {1: f"sha256_search_{search_term.strip().upper()}" if isinstance(gl.json_data, dict) else 'error'}
            else:
                api_url = create_api_url(content_type, sort_type, period_type, use_search_term, base_filter, only_liked, tile_count, search_term, nsfw, exact_search)
                gl.url_list = {1: api_url}
                gl.json_data = request_civit_api(api_url)
        else:
            api_url = gl.url_list.get(current_page)
    else:
        api_url = gl.url_list.get(current_page)
        gl.from_update_tab = True
        if api_url and not api_url.startswith('sha256_search_'):
            gl.json_data = request_civit_api(api_url)

    max_page = 1
    model_list = []
    hasPrev, hasNext = False, False

    if not isinstance(gl.json_data, dict):
        HTML = api_error_msg(gl.json_data)
    else:
        gl.json_data = insert_metadata(1)

        metadata = gl.json_data['metadata']
        hasNext = 'nextPage' in metadata
        hasPrev = 'prevPage' in metadata

        # Check for empty results when searching by User Name
        if use_search_term == 'User name' and (not gl.json_data.get('items') or len(gl.json_data['items']) == 0):
            HTML = api_error_msg('user_not_found')
        else:
            for item in gl.json_data['items']:
                if len(item['modelVersions']) > 0:
                    model_list.append(f"{item['name']} ({item['id']})")

            max_page = max(gl.url_list.keys())
            HTML = model_list_html(gl.json_data)

    return (
        gr.Dropdown.update(choices=model_list, value='', interactive=True),     # Model List
        gr.Dropdown.update(choices=[], value=''),                               # Version List
        gr.HTML.update(value=HTML),                                             # HTML Tiles
        gr.Button.update(interactive=hasPrev),                                  # Prev Page Button
        gr.Button.update(interactive=hasNext),                                  # Next Page Button
        gr.Slider.update(value=current_page, maximum=max_page),                 # Page Slider
        gr.Button.update(interactive=False),                                    # Save Tags
        gr.Button.update(interactive=False),                                    # Save Images
        gr.Button.update(interactive=False, visible=False if gl.isDownloading else True),  # Download Button
        gr.Button.update(interactive=False, visible=False),                     # Delete Button
        gr.Textbox.update(interactive=False, value=None, visible=True),         # Install Path
        gr.Dropdown.update(choices=[], value='', interactive=False),            # Sub Folder List
        gr.Dropdown.update(choices=[], value='', interactive=False),            # File List
        gr.HTML.update(value='<div style="min-height: 0px;"></div>'),           # Preview HTML
        gr.Textbox.update(value=None),                                          # Trained Tags
        gr.Textbox.update(value=None),                                          # Base Model
        gr.Textbox.update(value=None)                                           # Model Filename
    )

def prev_model_page(content_type, sort_type, period_type, use_search_term, search_term, current_page, base_filter, only_liked, nsfw, exact_search, tile_count):
    return next_model_page(content_type, sort_type, period_type, use_search_term, search_term, current_page, base_filter, only_liked, nsfw, exact_search, tile_count, isNext=False)

def next_model_page(content_type, sort_type, period_type, use_search_term, search_term, current_page, base_filter, only_liked, nsfw, exact_search, tile_count, isNext=True):

    current_inputs = (content_type, sort_type, period_type, use_search_term, search_term, tile_count, base_filter, nsfw, exact_search)
    if current_inputs != gl.previous_inputs and gl.previous_inputs != None:
        return initial_model_page(content_type, sort_type, period_type, use_search_term, search_term, current_page, base_filter, only_liked, nsfw, exact_search, tile_count)

    api_url = create_api_url(isNext=isNext)
    gl.json_data = request_civit_api(api_url)

    next_page = current_page
    model_list = []
    max_page = 1
    hasPrev, hasNext = False, False

    if not isinstance(gl.json_data, dict):
        HTML = api_error_msg(gl.json_data)
    else:
        next_page = current_page + 1 if isNext else current_page - 1

        gl.json_data = insert_metadata(next_page, api_url)

        metadata = gl.json_data['metadata']
        hasNext = 'nextPage' in metadata
        hasPrev = 'prevPage' in metadata

        for item in gl.json_data['items']:
            if len(item['modelVersions']) > 0:
                model_list.append(f"{item['name']} ({item['id']})")

        max_page = max(gl.url_list.keys())
        HTML = model_list_html(gl.json_data)

    return (
        gr.Dropdown.update(choices=model_list, value='', interactive=True),  # Model List
        gr.Dropdown.update(choices=[], value=''),  # Version List
        gr.HTML.update(value=HTML),  # HTML Tiles
        gr.Button.update(interactive=hasPrev),  # Prev Page Button
        gr.Button.update(interactive=hasNext),  # Next Page Button
        gr.Slider.update(value=next_page, maximum=max_page),  # Current Page
        gr.Button.update(interactive=False),  # Save Tags
        gr.Button.update(interactive=False),  # Save Images
        gr.Button.update(interactive=False, visible=False if gl.isDownloading else True),  # Download Button
        gr.Button.update(interactive=False, visible=False),  # Delete Button
        gr.Textbox.update(interactive=False, value=None),  # Install Path
        gr.Dropdown.update(choices=[], value='', interactive=False),  # Sub Folder List
        gr.Dropdown.update(choices=[], value='', interactive=False),  # File List
        gr.HTML.update(value='<div style="min-height: 0px;"></div>'),  # Preview HTML
        gr.Textbox.update(value=None),  # Trained Tags
        gr.Textbox.update(value=None),  # Base Model
        gr.Textbox.update(value=None)  # Model Filename
    )

def insert_metadata(page_nr, api_url=None):
    metadata = gl.json_data['metadata']

    if not metadata.get('prevPage', None) and page_nr > 1:
        metadata['prevPage'] = gl.url_list.get((page_nr - 1))

    if gl.from_update_tab:
        if gl.url_list.get((page_nr + 1), None):
            metadata['nextPage'] = gl.url_list.get((page_nr + 1))

    elif page_nr not in gl.url_list:
        gl.url_list[page_nr] = api_url

    return gl.json_data

## === ANXETY EDITs ===
def update_model_versions(model_id, json_input=None):
    if json_input:
        api_json = json_input
    else:
        api_json = gl.json_data

    for item in api_json['items']:
        if int(item['id']) == int(model_id):
            content_type = item['type']
            desc = item.get('description', 'None')

            versions_dict = defaultdict(list)
            installed_versions = set()

            model_folder = os.path.join(contenttype_folder(content_type, desc))
            gl.main_folder = model_folder
            versions = item['modelVersions']

            version_files = set()
            for version in versions:
                versions_dict[version['name']].append(item['name'])
                for version_file in version['files']:
                    file_sha256 = normalize_sha256(version_file.get('hashes', {}).get('SHA256', ''))
                    version_filename = version_file['name']
                    version_files.add((version['name'], version_filename, file_sha256))

            for root, _, files in os.walk(model_folder, followlinks=True):
                for file in files:
                    if file.endswith('.json'):
                        try:
                            json_path = os.path.join(root, file)
                            with open(json_path, 'r', encoding='utf-8') as f:
                                json_data = json.load(f)
                                if isinstance(json_data, dict):
                                    sha256 = normalize_sha256(json_data.get('sha256'))
                                    if sha256:
                                        for version_name, _, file_sha256 in version_files:
                                            if sha256 == file_sha256:
                                                installed_versions.add(version_name)
                                                break
                        except Exception as e:
                            print(f"failed to read: '{file}': {e}")

                    # filename_check
                    for version_name, version_filename, _ in version_files:
                        if file.lower() == version_filename.lower():
                            installed_versions.add(version_name)
                            break

            version_names = list(versions_dict.keys())
            # Build display names with [Installed] and (Early Access) if applicable
            display_version_names = []
            for v in version_names:
                # Find the version object for this name
                version_obj = next((ver for ver in versions if ver['name'] == v), None)
                name = v
                installed = v in installed_versions
                early_access = is_early_access(version_obj) if version_obj else False
                if installed:
                    name += ' [Installed]'
                if early_access:
                    name += ' (Early Access)'
                display_version_names.append(name)
            default_installed = next((name for name in display_version_names if '[Installed]' in name), None)
            default_value = default_installed or (display_version_names[0] if display_version_names else None)

            return gr.Dropdown.update(choices=display_version_names, value=default_value, interactive=True)  # Version List

    return gr.Dropdown.update(choices=[], value=None, interactive=False)  # Version List

def cleaned_name(file_name):
    if platform.system() == "Windows":
        illegal_chars_pattern = r'[\\/:*?"<>|]'
    else:
        illegal_chars_pattern = r'/'

    name, extension = os.path.splitext(file_name)
    clean_name = re.sub(illegal_chars_pattern, '', name)
    clean_name = re.sub(r'\s+', ' ', clean_name.strip())

    return f"{clean_name}{extension}"

def fetch_and_process_image(image_url):
    proxies, ssl = get_proxies()
    try:
        parsed_url = urllib.parse.urlparse(image_url)
        if parsed_url.scheme and parsed_url.netloc:
            response = requests.get(image_url, proxies=proxies, verify=ssl)
            if response.status_code == 200:
                image = Image.open(BytesIO(response.content))
                geninfo, _ = read_info_from_image(image)
                return geninfo
        else:
            image = Image.open(image_url)
            geninfo, _ = read_info_from_image(image)
            return geninfo
    except:
        return None

def extract_model_info(input_string):
    last_open_parenthesis = input_string.rfind('(')
    last_close_parenthesis = input_string.rfind(')')

    name = input_string[:last_open_parenthesis].strip()
    id_number = input_string[last_open_parenthesis + 1:last_close_parenthesis]

    return name, int(id_number)

def update_model_info(model_string=None, model_version=None, only_html=False, input_id=None, json_input=None, from_preview=False):
    video_playback = getattr(opts, 'video_playback', True)
    meta_btn = getattr(opts, 'individual_meta_btn', True)
    playback = ''
    if video_playback:
        playback = 'autoplay loop'

    if json_input:
        api_data = json_input
    else:
        api_data = gl.json_data

    BtnDownInt = True
    BtnDel = False
    BtnImage = False
    model_id = None

    if not input_id:
        _, model_id = extract_model_info(model_string)
    else:
        model_id = input_id

    if model_version and '[Installed]' in model_version:
        model_version = model_version.replace(' [Installed]', '')
    if model_id:
        output_html = ''
        output_training = ''
        output_basemodel = ''
        img_html = ''
        dl_dict = {}
        is_LORA = False
        file_list = []
        file_dict = []
        default_file = None
        model_filename = None
        sha256_value = None
        for item in api_data['items']:
            if int(item['id']) == int(model_id):
                content_type = item['type']
                if content_type == 'LORA':
                    is_LORA = True
                desc = item['description']
                model_name = item['name']
                model_folder = os.path.join(contenttype_folder(content_type, desc))
                model_uploader = None
                uploader_avatar = None

                # Use a dedicated function to check if the model is NSFW
                is_nsfw = is_model_nsfw(item)

                creator = item.get('creator', None)
                if creator:
                    model_uploader = creator.get('username', None)
                    uploader_avatar = creator.get('image', None)
                if not model_uploader:
                    model_uploader = 'User not found'
                    uploader_avatar = 'https://rawcdn.githack.com/gist/BlafKing/8d3f7a19e3f72cfddab46ae835037ee6/raw/296e81afbdd268200278beef478f3018b15936de/profile_placeholder.svg'
                uploader_avatar = (f'<div class="avatar"><img src={uploader_avatar}></div>')
                tags = item.get('tags', '')
                model_desc = item.get('description', '')
                if model_desc:
                    model_desc = model_desc.replace('<img', '<img style="max-width: -webkit-fill-available;"')
                    model_desc = model_desc.replace('<code>', '<code style="text-wrap: wrap">')
                if model_version is None:
                    selected_version = item['modelVersions'][0]
                else:
                    selected_version = None
                    for model in item['modelVersions']:
                        if model['name'] == model_version:
                            selected_version = model
                            break
                    if selected_version == None and item['modelVersions']:
                        selected_version = item['modelVersions'][0]  # fallback to first version

                model_availability = selected_version.get('availability', 'Unknown')
                published_at = selected_version.get('publishedAt')
                model_date_published = published_at.split('T')[0] if published_at else 'Unknown'
                version_name = selected_version['name']
                version_id = selected_version['id']
                version_about = selected_version.get('description', '')
                if version_about is not None and version_about.strip():
                    version_about = version_about.replace('<code>', '<code style="text-wrap: wrap">')
                    if model_desc:
                        model_desc += '\n<hr>\n<h3>About this version:</h3>\n' + version_about.strip()
                    else:
                        model_desc = '<h3>About this version:</h3>\n' + version_about.strip()

                if selected_version['trainedWords']:
                    output_training = ','.join(selected_version['trainedWords'])
                    output_training = re.sub(r'<[^>]*:[^>]*>', '', output_training)
                    output_training = re.sub(r', ?', ', ', output_training)
                    output_training = output_training.strip(', ')
                if selected_version['baseModel']:
                    output_basemodel = selected_version['baseModel']
                for file in selected_version['files']:
                    dl_dict[file['name']] = file['downloadUrl']

                    if not model_filename:
                        model_filename = file['name']
                        dl_url = file['downloadUrl']
                        gl.json_info = item
                        sha256_value = normalize_sha256(file['hashes'].get('SHA256')) or 'Unknown'

                    size = file['metadata'].get('size', 'Unknown')
                    format = file['metadata'].get('format', 'Unknown')
                    fp = file['metadata'].get('fp', 'Unknown')
                    sizeKB = file.get('sizeKB', 0) * 1024
                    filesize = _download.convert_size(sizeKB)

                    unique_file_name = f"{size} {format} {fp} ({filesize})"
                    is_primary = file.get('primary', False)
                    file_list.append(unique_file_name)
                    file_dict.append({
                        'format': format,
                        'sizeKB': sizeKB
                    })
                    if is_primary:
                        default_file = unique_file_name
                        model_filename = file['name']
                        dl_url = file['downloadUrl']
                        gl.json_info = item
                        sha256_value = normalize_sha256(file['hashes'].get('SHA256')) or 'Unknown'

                safe_tensor_found = False
                pickle_tensor_found = False
                if is_LORA and file_dict:
                    for file_info in file_dict:
                        file_format = file_info.get('format', '')
                        if 'SafeTensor' in file_format:
                            safe_tensor_found = True
                        if 'PickleTensor' in file_format:
                            pickle_tensor_found = True

                    if safe_tensor_found and pickle_tensor_found:
                        if 'PickleTensor' in file_dict[0].get('format', ''):
                            if file_dict[0].get('sizeKB', 0) <= 100:
                                model_folder = os.path.join(contenttype_folder('TextualInversion'))

                model_url = selected_version.get('downloadUrl', '')
                model_main_url = f"https://civitai.com/models/{item['id']}"

                url = f"https://civitai.com/api/v1/model-versions/{selected_version['id']}"
                api_version = request_civit_api(url)

                ## === ANXETY EDITs ===
                # --- HTML Generation ---
                BtnImage = True
                # Build image block
                img_html = '<div class="sampleimgs">'

                key_map = {
                    'prompt': 'Prompt',
                    'negativePrompt': 'Negative Prompt',
                    'Model': 'Model',
                    'sampler': 'Sampler',
                    'steps': 'Steps',
                    'cfgScale': 'CFG Scale',
                    'clipSkip': 'Clip Skip',
                    'seed': 'Seed',
                    'Size': 'Size',
                }
                preferred_order = ['prompt', 'negativePrompt', 'Model', 'sampler', 'steps', 'cfgScale', 'Clip skip', 'seed', 'Size']

                # Check if api_version is a valid dictionary (not an error string)
                if isinstance(api_version, dict) and 'images' in api_version:
                    for idx, pic in enumerate(api_version['images']):
                        index = f"preview_{idx}" if from_preview else idx
                        prompt_dict = pic.get('meta', {}) or {}
                        image_url = re.sub(r'/width=\d+', f"/width={pic.get('width', '')}", pic['url'])
                        is_video = pic.get('type') == 'video'

                        img_html += (
                            f'<div class="image-block">'
                            f'<div class="civitai-image-container">'
                        )

                        if is_video:
                            video_url = image_url.replace('width=', 'transcode=true,width=')
                            img_html += (
                                f'<video class="preview-media" data-sampleimg="true" {playback} muted playsinline onclick="openImageViewer(\'{escape(video_url)}\', \'video\')">'
                                f'<source src="{video_url}" type="video/mp4"></video>'
                            )
                            meta_button = False
                            prompt_dict = {}
                        else:
                            img_html += (
                                f'<img class="preview-media" data-sampleimg="true" src="{image_url}" alt="Model preview" onclick="openImageViewer(\'{escape(image_url)}\', \'image\')">'
                            )
                            meta_button = bool(prompt_dict.get('prompt'))

                        if meta_button:
                            img_html += (
                                '<div class="civitai_txt2img">'
                                f'<label onclick="sendImgUrl(\'{escape(image_url)}\')" class="civitai-txt2img-btn">Send to txt2img</label>'
                                '</div>'
                            )
                        img_html += '</div>'  # close .civitai-image-container

                        if prompt_dict:
                            img_html += (
                                '<div id="image_info">'
                                '<dl>'
                            )
                            for key in preferred_order:
                                if key in prompt_dict:
                                    value = prompt_dict[key]
                                    key_disp = key_map.get(key, key)
                                    if meta_btn:
                                        img_html += (
                                            f'<div class="civitai-meta-btn" data-key="{key}" onclick="metaToTxt2Img(\'{escape(str(key_disp))}\', this)">'
                                            f'<dt>{escape(str(key_disp))}</dt><dd>{escape(str(value))}</dd></div>'
                                        )
                                    else:
                                        img_html += (
                                            f'<div class="civitai-meta" data-key="{key}"><dt>{escape(str(key_disp))}</dt><dd>{escape(str(value))}</dd></div>'
                                        )
                            # Check if there are remaining keys in meta
                            remaining_keys = [k for k in prompt_dict if k not in preferred_order]

                            # Add the rest
                            if remaining_keys:
                                img_html += (
                                    '<div class="tabs">'
                                    '<div class="tab">'
                                    f'<input type="checkbox" class="accordionCheckbox" id="chck{index}">'
                                    f'<label class="tab-label" for="chck{index}">More details...</label>'
                                    '<div class="tab-content">'
                                )
                                for key in remaining_keys:
                                    value = prompt_dict[key]
                                    img_html += (
                                        f'<div class="civitai-meta" data-key="{key}"><dt>{escape(str(key).capitalize())}</dt><dd>{escape(str(value))}</dd></div>'
                                    )
                                img_html += '</div></div></div>'
                            img_html += '</dl></div>'
                        else:
                            # Show beautiful empty state when no metadata is available
                            if is_video:
                                no_meta_type = "video"
                                icon_svg = (
                                    '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
                                    '<rect x="3" y="5" width="18" height="14" rx="2" ry="2"></rect>'
                                    '<polygon points="10,9 16,12 10,15"></polygon>'
                                    '</svg>'
                                )
                            else:
                                no_meta_type = "image"
                                icon_svg = (
                                    '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
                                    '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"></path>'
                                    '<polyline points="14,2 14,8 20,8"></polyline>'
                                    '<path d="M12 18v-4"></path>'
                                    '<path d="M12 10h.01"></path>'
                                    '</svg>'
                                )
                            img_html += (
                                '<div class="image-metadata-empty">'
                                '<div class="empty-state-icon">'
                                f'{icon_svg}'
                                '</div>'
                                '<div class="empty-state-text">'
                                f'<h4>No metadata available</h4>'
                                f'<p>No generation settings are available for this {no_meta_type}.</p>'
                                '</div>'
                                '</div>'
                            )
                        img_html += '</div>'  # close .image-block
                    img_html += '</div>'
                else:
                    # Handle API error case - show error message instead of images
                    img_html += (
                        '<div class="image-block-empty">'
                            '<div class="image-metadata-empty">'
                                '<div class="empty-state-icon">'
                                    '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
                                    '<circle cx="12" cy="12" r="10"></circle>'
                                    '<line x1="12" y1="8" x2="12" y2="12"></line>'
                                    '<line x1="12" y1="16" x2="12.01" y2="16"></line>'
                                    '</svg>'
                                '</div>'
                                '<div class="empty-state-text">'
                                    '<h4>Unable to load preview images</h4>'
                                    '<p>There was an error loading the model preview. The model information may still be available.</p>'
                                '</div>'
                            '</div>'
                        '</div>'
                    )
                img_html += '</div>'

                # (Image viewer overlay is created dynamically in JavaScript)

                tags_html = ''.join([f'<span class="civitai-tag">{escape(str(tag))}</span>' for tag in tags])

                # Build permissions block
                allow_svg = '<svg width="16" height="16" viewBox="0 1.5 24 24" stroke-width="4" stroke-linecap="round" stroke="lime"><path d="M5 12l5 5l10 -10"></path></svg>'
                deny_svg = '<svg width="16" height="16" viewBox="0 1.5 24 24" stroke-width="4" stroke-linecap="round" stroke="red"><path d="M18 6l-12 12"></path><path d="M6 6l12 12"></path></svg>'
                allowCommercialUse = item.get('allowCommercialUse', [])

                perms_html = (
                    '<p>'
                        f'{allow_svg if item.get("allowNoCredit") else deny_svg} Use the model without crediting the creator<br/>'
                        f'{allow_svg if "Image" in allowCommercialUse else deny_svg} Sell images they generate<br/>'
                        f'{allow_svg if "Rent" in allowCommercialUse else deny_svg} Run on services that generate images for money<br/>'
                        f'{allow_svg if "RentCivit" in allowCommercialUse else deny_svg} Run on Civitai<br/>'
                        f'{allow_svg if item.get("allowDerivatives") else deny_svg} Share merges using this model<br/>'
                        f'{allow_svg if "Sell" in allowCommercialUse else deny_svg} Sell this model or merges using this model<br/>'
                        f'{allow_svg if item.get("allowDifferentLicense") else deny_svg} Have different permissions when sharing merges'
                    '</p>'
                )

                # Build header block
                model_page = (
                    '<div class="model-page-line">'
                        '<span class="page-label">Model Page:</span>'
                        f'<a href={model_main_url}?modelVersionId={selected_version["id"]} target="_blank">{escape(str(model_name))}</a>'
                    '</div>'
                )

                if not creator or model_uploader == 'User not found':
                    uploader_page = (
                        '<div class="model-uploader-line">'
                            '<span class="uploader-label">Uploaded Unknown:</span>'
                            f'<span>{escape(str(model_uploader))}</span>'
                            f'{uploader_avatar}'
                        '</div>'
                    )
                else:
                    uploader_page = (
                        '<div class="model-uploader-line">'
                            '<span class="uploader-label">Uploaded by:</span>'
                            f'<a href="https://civitai.com/user/{escape(str(model_uploader))}" target="_blank">{escape(str(model_uploader))}</a>'
                            f'{uploader_avatar}'
                        '</div>'
                    )

                # Build version info block
                version_info = (
                    '<div class="version-info-block">'
                        '<h3 class="block-header">Version Information</h3>'
                        '<dl>'
                            '<dt>Type</dt>'
                            f'<dd>{get_display_type(content_type)}</dd>'
                            '<dt>Version</dt>'
                            f'<dd>{escape(str(model_version))}</dd>'
                            '<dt>Base Model</dt>'
                            f'<dd>{escape(str(output_basemodel))}</dd>'
                            '<dt>Availability</dt>'
                            f'<dd>{model_availability}</dd>'
                            '<dt>Published</dt>'
                            f'<dd>{model_date_published}</dd>'
                            '<dt>CivitAI Tags</dt>'
                            '<dd>'
                                '<div class="civitai-tags-container">'
                                    f'{tags_html}'
                                '</div>'
                            '</dd>'
                            f'{"<dt>Download Link</dt>" if model_url else ""}'
                            f'{f"<dd><a href={model_url} target=_blank>{model_url}</a></dd>" if model_url else ""}'
                        '</dl>'
                    '</div>'
                )

                # Build permissions block
                version_permissions = (
                    '<div class="permissions-block">'
                        '<h3 class="block-header">Permissions</h3>'
                        f'{perms_html}'
                    '</div>'
                )

                # Build description section
                prefix = "preview-" if from_preview else ""
                description_section = (
                    '<div class="description-block">'
                        '<h2 class="block-header">Model Description</h2>'
                        '<div class="description-wrapper">'
                            f'<div class="description-content" id="{prefix}description-content">'
                                f'{model_desc}'
                            '</div>'
                            f'<div class="description-overlay" id="{prefix}description-overlay"></div>'
                            f'<button class="description-toggle-btn" id="{prefix}description-toggle-btn" onclick="toggleDescription(\'{prefix}\')">Show More</button>'
                        '</div>'
                    '</div>'
                )

                # Build main HTML structure
                output_html = (
                    '<div class="main-container">'
                        '<div class="info-section">'
                            '<div class="header-block">'
                                f'{model_page}'
                                '<div class="uploader-divider"></div>'
                                f'{uploader_page}'
                            '</div>'
                            '<div class="info-permissions-container">'
                                f'{version_info}'
                                f'{version_permissions}'
                            '</div>'
                            f'{description_section}'
                        '</div>'
                        '<div class="images-section">'
                            f'{img_html}'
                        '</div>'
                    '</div>'
                )

        if only_html:
            return output_html

        folder_location = 'None'
        default_subfolder = 'None'
        sub_folders = _file.getSubfolders(
            model_folder,
            output_basemodel,
            is_nsfw,
            model_uploader,
            model_name,
            model_id,
            version_name,
            version_id
        )

        for root, dirs, files in os.walk(model_folder, followlinks=True):
            for filename in files:
                if filename.endswith('.json'):
                    json_file_path = os.path.join(root, filename)
                    with open(json_file_path, 'r', encoding='utf-8') as f:
                        try:
                            data = json.load(f)
                            sha256 = normalize_sha256(data.get('sha256'))
                            if sha256 and sha256 == sha256_value:
                                folder_location = root
                                BtnDownInt = False
                                BtnDel = True
                                break
                        except Exception as e:
                            print(f"Error decoding JSON: {str(e)}")
            else:
                # filename_check
                for filename in files:
                    if filename.lower() == model_filename.lower() or filename.lower() == cleaned_name(model_filename).lower():
                        folder_location = root
                        BtnDownInt = False
                        BtnDel = True
                        break

            if folder_location != 'None':
                break

        default_subfolder = sub_folder_value(content_type, desc)
        if default_subfolder != 'None':
            default_subfolder = _file.convertCustomFolder(default_subfolder, output_basemodel, is_nsfw, model_uploader, model_name, model_id, version_name, version_id)
        if folder_location == 'None':
            folder_location = model_folder
            if default_subfolder != 'None':
                folder_path = str(folder_location) + default_subfolder
            else:
                folder_path = str(folder_location)
        else:
            folder_path = folder_location

        relative_path = os.path.relpath(folder_location, model_folder)
        default_subfolder = f"{os.sep}{relative_path}" if relative_path != '.' else default_subfolder if BtnDel == False else 'None'
        if gl.isDownloading:
            item = gl.download_queue[0]
            if int(model_id) == int(item['model_id']):
                BtnDel = False
        BtnDownTxt = 'Download model'
        if len(gl.download_queue) > 0:
            BtnDownTxt = 'Add to queue'
            for item in gl.download_queue:
                if item['version_name'] == model_version and int(item['model_id']) == int(model_id):
                    BtnDownInt = False
                    break

        return (
            gr.HTML.update(value=output_html),                                                      # Preview HTML
            gr.Textbox.update(value=output_training, interactive=True),                             # Trained Tags
            gr.Textbox.update(value=output_basemodel),                                              # Base Model Number
            gr.Button.update(visible=False if BtnDel else True, interactive=BtnDownInt, value=BtnDownTxt),  # Download Button
            gr.Button.update(interactive=BtnImage),                                                 # Images Button
            gr.Button.update(visible=BtnDel, interactive=BtnDel),                                   # Delete Button
            gr.Dropdown.update(choices=file_list, value=default_file, interactive=True),            # File List
            gr.Textbox.update(value=cleaned_name(model_filename), interactive=True),                # Model File Name
            gr.Textbox.update(value=dl_url),                                                        # Download URL
            gr.Textbox.update(value=model_id),                                                      # Model ID
            gr.Textbox.update(value=sha256_value),                                                  # SHA256
            gr.Textbox.update(interactive=True, value=folder_path if model_name else None),         # Install Path
            gr.Dropdown.update(choices=sub_folders, value=default_subfolder, interactive=True)      # Sub Folder List
        )
    else:
        return (
            gr.HTML.update(value=None),                                         # Preview HTML
            gr.Textbox.update(value=None, interactive=False),                   # Trained Tags
            gr.Textbox.update(value=''),                                        # Base Model Number
            gr.Button.update(visible=False if BtnDel else True, value='Download model'),  # Download Button
            gr.Button.update(interactive=False),                                # Images Button
            gr.Button.update(visible=BtnDel, interactive=BtnDel),               # Delete Button
            gr.Dropdown.update(choices=None, value=None, interactive=False),    # File List
            gr.Textbox.update(value=None, interactive=False),                   # Model File Name
            gr.Textbox.update(value=None),                                      # Download URL
            gr.Textbox.update(value=None),                                      # Model ID
            gr.Textbox.update(value=None),                                      # SHA256
            gr.Textbox.update(interactive=False, value=None),                   # Install Path
            gr.Dropdown.update(choices=None, value=None, interactive=False)     # Sub Folder List
        )

def sub_folder_value(content_type, desc=None):
    if content_type == 'LORA':
        folder = getattr(opts, 'LORA_default_subfolder', 'None')
    elif content_type == 'Upscaler':
        for upscale_type in ['SWINIR', 'REALESRGAN', 'GFPGAN', 'BSRGAN']:
            if upscale_type in desc:
                folder = getattr(opts, f"{upscale_type}_default_subfolder", 'None')
        folder = getattr(opts, 'ESRGAN_default_subfolder', 'None')
    else:
        folder = getattr(opts, f"{content_type}_default_subfolder", 'None')
    if folder == None:
        return 'None'
    return folder

def update_file_info(model_string, model_version, file_metadata):
    file_list = []
    is_LORA = False
    embed_check = False
    model_name = None
    model_id = None
    model_name, model_id = extract_model_info(model_string)

    if model_version and '[Installed]' in model_version:
        model_version = model_version.replace(' [Installed]', '')
    if model_id and model_version:
        for item in gl.json_data['items']:
            if int(item['id']) == int(model_id):
                content_type = item['type']
                if content_type == 'LORA':
                    is_LORA = True
                desc = item['description']
                for model in item['modelVersions']:
                    if model['name'] == model_version:
                        for file in model['files']:
                            size = file['metadata'].get('size', 'Unknown')
                            format = file['metadata'].get('format', 'Unknown')
                            unique_file_name = f"{size} {format}"
                            file_list.append(unique_file_name)
                            pass

                        if is_LORA and file_list:
                            extracted_formats = [file.split(' ')[1] for file in file_list]
                            if 'SafeTensor' in extracted_formats and 'PickleTensor' in extracted_formats:
                                embed_check = True

                        for file in model['files']:
                            model_id = item['id']
                            file_name = file.get('name', 'Unknown')
                            sha256 = normalize_sha256(file['hashes'].get('SHA256')) or 'Unknown'
                            metadata = file.get('metadata', {})
                            file_size = metadata.get('size', 'Unknown')
                            file_format = metadata.get('format', 'Unknown')
                            file_fp = metadata.get('fp', 'Unknown')
                            sizeKB = file.get('sizeKB', 0)
                            sizeB = sizeKB * 1024
                            filesize = _download.convert_size(sizeB)

                            if f"{file_size} {file_format} {file_fp} ({filesize})" == file_metadata:
                                installed = False
                                folder_location = 'None'
                                model_folder = os.path.join(contenttype_folder(content_type, desc))
                                if embed_check and file_format == 'PickleTensor':
                                    if sizeKB <= 100:
                                        model_folder = os.path.join(contenttype_folder('TextualInversion'))
                                dl_url = file['downloadUrl']
                                gl.json_info = item
                                for root, _, files in os.walk(model_folder, followlinks=True):
                                    if file_name in files:
                                        installed = True
                                        folder_location = root
                                        break

                                if not installed:
                                    for root, _, files in os.walk(model_folder, followlinks=True):
                                        for filename in files:
                                            if filename.endswith('.json'):
                                                with open(os.path.join(root, filename), 'r', encoding='utf-8') as f:
                                                    try:
                                                        data = json.load(f)
                                                        sha256_value = normalize_sha256(data.get('sha256'))
                                                        if sha256_value and sha256_value == sha256:
                                                            folder_location = root
                                                            installed = True
                                                            break
                                                    except Exception as e:
                                                        print(f"Error decoding JSON: {str(e)}")
                                default_sub = sub_folder_value(content_type, desc)
                                if folder_location == 'None':
                                    folder_location = model_folder
                                    if default_sub != 'None':
                                        folder_path = str(folder_location) + default_sub
                                    else:
                                        folder_path = str(folder_location)
                                else:
                                    folder_path = folder_location
                                relative_path = os.path.relpath(folder_location, model_folder)
                                default_subfolder = f"{os.sep}{relative_path}" if relative_path != '.' else default_sub if installed == False else 'None'
                                BtnDownInt = not installed
                                BtnDownTxt = 'Download model'
                                if len(gl.download_queue) > 0:
                                    BtnDownTxt = 'Add to queue'
                                    for item in gl.download_queue:
                                        if item['version_name'] == model_version:
                                            BtnDownInt = False
                                            break

                                return (
                                    gr.Textbox.update(value=cleaned_name(file['name']), interactive=True),  # Model File Name Textbox
                                    gr.Textbox.update(value=dl_url),  # Download URL Textbox
                                    gr.Textbox.update(value=model_id),  # Model ID Textbox
                                    gr.Textbox.update(value=sha256),  # sha256 textbox
                                    gr.Button.update(interactive=BtnDownInt, visible=False if installed else True, value=BtnDownTxt),  # Download Button
                                    gr.Button.update(interactive=True if installed else False, visible=True if installed else False),  # Delete Button
                                    gr.Textbox.update(interactive=True, value=folder_path if model_name else None),  # Install Path
                                    gr.Dropdown.update(value=default_subfolder, interactive=True)  # Sub Folder List
                                )

    return (
        gr.Textbox.update(value=None, interactive=False),  # Model File Name Textbox
        gr.Textbox.update(value=None),  # Download URL Textbox
        gr.Textbox.update(value=None),  # Model ID Textbox
        gr.Textbox.update(value=None),  # sha256 textbox
        gr.Button.update(interactive=False, visible=True),  # Download Button
        gr.Button.update(interactive=False, visible=False),  # Delete Button
        gr.Textbox.update(interactive=False, value=None),  # Install Path
        gr.Dropdown.update(choices=None, value=None, interactive=False)  # Sub Folder List
    )

def get_proxies():
    custom_proxy = getattr(opts, 'custom_civitai_proxy', '')
    disable_ssl = getattr(opts, 'disable_sll_proxy', False)
    cabundle_path = getattr(opts, 'cabundle_path_proxy', '')

    ssl = True
    proxies = {}
    if custom_proxy:
        if not disable_ssl:
            if cabundle_path:
                ssl = os.path.exists(cabundle_path)  # Check if cabundle_path is a valid file
        else:
            ssl = False
        proxies = {
            'http': custom_proxy,
            'https': custom_proxy,
        }
    return proxies, ssl

def get_headers(referer=None, no_api=None):
    api_key = getattr(opts, 'custom_api_key', '')
    headers = {
        'Connection': 'keep-alive',
        'Sec-Ch-Ua-Platform': 'Windows',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Content-Type': 'application/json'
    }
    if referer:
        headers['Referer'] = f"https://civitai.com/models/{referer}"
    if api_key and not no_api:
        headers['Authorization'] = f"Bearer {api_key}"

    return headers

def request_civit_api(api_url=None, skip_error_check=False):
    headers = get_headers()
    proxies, ssl = get_proxies()
    try:
        response = requests.get(api_url, headers=headers, timeout=(60, 30), proxies=proxies, verify=ssl)
        if skip_error_check:
            response.encoding = 'utf-8'
            data = json.loads(response.text)
            return data
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"Model version not found (404): {api_url}")
            return 'not_found'
        else:
            print(f"HTTP Error {e.response.status_code}: {e}")
            return 'error'
    except requests.exceptions.Timeout as e:
        print('The request timed out. Please try again later.')
        return 'timeout'
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")
        return 'error'
    else:
        response.encoding = 'utf-8'
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            print(response.text)
            print('The CivitAI servers are currently offline. Please try again later.')
            return 'offline'
    return data

## === ANXETY EDITs ===
def api_error_msg(input_string):
    div = '<div style="color: white; font-family: var(--font); font-size: 24px; text-align: center; margin: 50px !important;">'
    if input_string == 'not_found':
        return div + 'Model ID not found on CivitAI.<br>Maybe the model doesn\'t exist on CivitAI?</div>'
    elif input_string == 'path_not_found':
        return div + 'Local model not found.<br>Could not locate the model path.</div>'
    elif input_string == 'timeout':
        return div + 'The CivitAI-API has timed out, please try again.<br>The servers might be too busy or down if the issue persists.'
    elif input_string == 'offline':
        return div + 'The CivitAI servers are currently offline.<br>Please try again later.'
    elif input_string == 'no_items':
        return div + 'Failed to retrieve any models from CivitAI<br>The servers might be too busy or down if the issue persists.'
    elif input_string == 'invalid_hash':
        return div + 'Invalid SHA256 hash format.<br>Please enter a valid 64-character hexadecimal hash.</div>'
    elif input_string == 'sha256_not_found':
        return div + 'No model found with this SHA256 hash.<br>The model might not exist on CivitAI or the hash might be incorrect.</div>'
    elif input_string == 'user_not_found':
        return div + 'No models found for this user on CivitAI.<br>Please check the correctness of the user name.'
    else:
        return div + 'The CivitAI-API failed to respond due to an error.<br>Check the logs for more details.'