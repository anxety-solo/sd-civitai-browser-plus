> [!Tip]
> **All changes in this fork are marked in the code as `=== ANXETY EDITs ===`**</br>
> _Treat this fork as an author's vision – it's not a fork that will fix absolutely every problem..._</br>
> You can report bugs to me and I'll try to fix them! :33

> [!Important]
> The extension has been significantly rewritten, so it may not process your existing models correctly.</br>
> _Be sure to update your HTML/JSON files..._</br>
> **Don’t forget to visit the extension settings!** _(default settings have been changed)_

## ✨ Key features and changes in this fork:

- Redesigned the model cards in the browser to give them a modern look :3
- Redesigned the sorting by date, make it neat in the form of bricks~
- Completely redesigned the model's HTML page, making it more compact and appealing.
- Added badges to model cards to display model type and NSFW status (if applicable)</br>
  _Note: Badges are only visible when `Tile Size` is set to 11 or higher._
- Model cards that require purchase are marked in gold.</br>
  _Note: The model type badge turns gold and displays a lightning icon :3_
- Removed `LoCon` and `DoRA` model types – now only `LORA` is supported~
- Added an `Exact search` toggle in the search filter for precise results.</br>
  _Note: This toggle simply adds double quotes `" "` to your search query~_
- Fixed the display logic for the "Select All" button – now it only appears when there are models to select.
- Implemented optional setting to change the resolution of model previews (Customize to your liking in settings - `Browser`)
- ~~Removed "outdated models" logic and scanning for updates (It does not correspond to reality)~~ _([Commit](https://github.com/anxety-solo/sd-civitai-browser-plus/commit/bd9b0bec465eb3803496ff002a330ae101fa2ae5))_</br>
  _Note: The logic for detecting outdated models has been reintroduced and rewritten using pattern matching for versions such as `vX` or `vX.X`_
- All CSS and JS files have been formatted using Prettier for consistent code style.
- Implemented encryption of image previews when images are saved (For Kaggle Only!)</br>
  _Note: The [Encrypt-Image](https://github.com/anxety-solo/sd-encrypt-image) extension must be installed to enable this feature._

#### 🔧 List of bugfixes and changes taken from Issues:

- Complete removal of HyperNetwork support - should fix problems with launching in [Forge-Classic](https://github.com/Haoming02/sd-webui-forge-classic) ([Issue #390](https://github.com/BlafKing/sd-civitai-browser-plus/issues/390))
- Added a clarifying message about Early Access to avoid confusion~ [Issue #383](https://github.com/BlafKing/sd-civitai-browser-plus/issues/383)
- Fixed the "Save Images" button – now it correctly saves model images [Issue #374](https://github.com/BlafKing/sd-civitai-browser-plus/issues/374)
- Models are now saved using their original file names, without any extra or random IDs [Issue #361](https://github.com/BlafKing/sd-civitai-browser-plus/issues/361)
- Fixed some issues with `sub_folder` [Issue #356](https://github.com/BlafKing/sd-civitai-browser-plus/issues/356)
- Fixed error `cvitai_subfolders.json does not exist` _(It's not a bug, it's a feature...)_ [Issue #349](https://github.com/BlafKing/sd-civitai-browser-plus/issues/349)
- Added an option to specify the number of model images to download (default: 16) [Issue #319](https://github.com/BlafKing/sd-civitai-browser-plus/issues/319)</br>
  _The number of images to save can be configured in settings - `Downloads`_
- Added a way to search for a model by its `hash` [Issue #309](https://github.com/BlafKing/sd-civitai-browser-plus/issues/309)

### 🖼️ Preview Browser (New Style):

<h6>> Browser Cards</h6>
<img src=".github/preview_browser_cards.png" alt="Preview Browser Cards" width="100%"/>
<h6>> Sorting Card Bricks <i>(when date sorting is enabled)</i></h6>
<img src=".github/preview_sort_by_date.png" alt="Preview Sorting Cards" width="100%"/>
<h6>> HTML Model Page</h6>
<img src=".github/preview_html.png" alt="Preview Model HTML Page" width="100%"/>

<div align="center">
    <h6>🎨 For better WebUI experience, I recommend using my theme - <a href="https://github.com/anxety-solo/anxety-theme">Anxety-Theme</a> :3</h6>
</div>

<h1></h1>

![CivitAI Browser+](https://github.com/BlafKing/sd-civitai-browser-plus/assets/9644716/95afcc41-56f0-4398-8779-51cb2a9e2f55)

---

### Extension for [Automatic1111's Stable Diffusion Web UI](https://github.com/AUTOMATIC1111/stable-diffusion-webui) and its forks :3

<h1>Features 🚀</h1>
<h3>Browse all models from CivitAI 🧩</h3>

* Explore a wide range of models at your fingertips.

<h3>Download any Model, any version, and any file 📥</h3>

* Get the specific model version and file you need hassle-free.
* Download queue to avoid waiting for finished downloads.

<h3>Automatically assign tags to installed models 🏷️</h3>

* Assign tags by scanning all installed models for automatic use in image generation.

<h3>Quick Model Info Access 📊</h3>

* A button for each model card in txt2img and img2img to load it into the extension.
* A button under each image in model info to send its generation info to txt2img.

<h3>High-speed downloads with Aria2 🚄</h3>

* Maximize your bandwidth for lightning-fast downloads.

<h3>Sleek and Intuitive User Interface 🖌️</h3>

* Enjoy a clutter-free, user-friendly interface, designed to enhance your experience.

<h1></h1>

# How to install 📘

1. Open WebUI
2. Navigate to "Extensions" → "Install from URL"
3. Paste `https://github.com/anxety-solo/sd-civitai-browser-plus`
4. Click "Install" and reload WebUI