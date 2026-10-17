const state = {
  screen: "home",
  returnScreen: "home",
  database: "",
  collectionPath: "",
  collectionName: "My collection",
  photoPage: 1,
  photoPageSize: 24,
  photoQuery: "",
  photoFilter: "all",
  photoSort: "newest",
  photoTotal: 0,
  loadedPhotos: [],
  photoLoading: false,
  photoRequestId: 0,
  peoplePage: 1,
  peoplePageSize: 24,
  peopleQuery: "",
  peopleTotal: 0,
  loadedPeople: [],
  peopleLoading: false,
  peopleRequestId: 0,
  people: [],
  galleryPerson: null,
  selectedPhotoPaths: new Set(),
  downloadBusy: false,
  viewerPhotos: [],
  viewerIndex: 0,
  viewerFeedback: {},
  cameraStream: null,
  lastTap: 0,
  indexBusy: false,
};

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);

function imageUrl(path) {
  return `/api/image?db=${encodeURIComponent(state.database)}&path=${encodeURIComponent(path)}`;
}

function faceUrl(face) {
  const box = face.bbox;
  return `/api/face?db=${encodeURIComponent(state.database)}&path=${encodeURIComponent(face.path)}`
    + `&x1=${box.x1}&y1=${box.y1}&x2=${box.x2}&y2=${box.y2}`;
}

function showNotice(element, message, kind = "info") {
  element.textContent = message;
  element.className = `notice ${kind}`;
}

function hideNotice(element) {
  element.className = "notice hidden";
  element.textContent = "";
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

function apiQuery(params = {}) {
  const search = new URLSearchParams({ db: state.database, ...params });
  return search.toString();
}

function setScreen(name) {
  state.screen = name;
  for (const screen of ["home", "all", "collection", "viewer"]) {
    $(`#screen-${screen}`).classList.toggle("hidden", screen !== name);
  }
  $("#bottom-nav").classList.toggle("hidden", name === "viewer");
  $("#back-button").classList.toggle("hidden", name === "home");
  $("#back-button").onclick = () => goBack();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function goBack() {
  if (state.screen === "viewer") {
    setScreen(state.returnScreen);
    return;
  }
  if (state.screen === "all" && state.galleryPerson) {
    openCollection();
    return;
  }
  setScreen("home");
}

async function initialize() {
  try {
    const config = await requestJson("/api/config");
    state.database = config.database;
    state.collectionPath = config.collection;
    $("#database").value = state.database;
    $("#collection-path").value = state.collectionPath;
    if (config.stats.backend && ["insightface", "dlib"].includes(config.stats.backend)) {
      $("#backend").value = config.stats.backend;
    }
    const title = await requestJson(`/api/collection-name?${apiQuery()}`);
    state.collectionName = title.name;
    $("#collection-name").value = title.name;
    $("#viewer-title").value = title.name;
    await loadFeedback();
  } catch (error) {
    showNotice($("#home-message"), error.message, "error");
  }
}

async function loadFeedback() {
  const result = await requestJson(`/api/feedback?${apiQuery()}`);
  state.viewerFeedback = result.feedback;
}

function renderPhotoGrid(photos, append = false) {
  const grid = $("#photo-grid");
  const markup = photos.map((photo) => state.galleryPerson
    ? `<div class="photo-tile selectable">
      <button class="photo-tile-open" data-path="${escapeHtml(photo.path)}" aria-label="Open photo">
      <img loading="lazy" src="${imageUrl(photo.path)}" alt="">
      <span class="tile-loading"><span class="spinner"></span></span>
      ${photo.faces ? `<span class="photo-count-badge">${photo.faces} face${photo.faces === 1 ? "" : "s"}</span>` : ""}
      </button>
      <label class="photo-select" aria-label="Select ${escapeHtml(photo.path.split(/[\\/]/).pop())}">
        <input type="checkbox" data-select-path="${escapeHtml(photo.path)}" ${state.selectedPhotoPaths.has(photo.path) ? "checked" : ""}>
        <span>Select</span>
      </label>
    </div>`
    : `<button class="photo-tile" data-path="${escapeHtml(photo.path)}" aria-label="Open photo">
      <img loading="lazy" src="${imageUrl(photo.path)}" alt="">
      <span class="tile-loading"><span class="spinner"></span></span>
      ${photo.faces ? `<span class="photo-count-badge">${photo.faces} face${photo.faces === 1 ? "" : "s"}</span>` : ""}
    </button>`).join("");
  if (append) grid.insertAdjacentHTML("beforeend", markup);
  else grid.innerHTML = markup;
  const images = append
    ? Array.from(grid.querySelectorAll("img")).slice(-photos.length)
    : Array.from(grid.querySelectorAll("img"));
  images.forEach((image) => {
    const loading = image.parentElement.querySelector(".tile-loading");
    image.addEventListener("load", () => {
      image.classList.add("loaded");
      loading.classList.add("hidden");
    }, { once: true });
    image.addEventListener("error", () => loading.classList.add("hidden"), { once: true });
    if (image.complete && image.naturalWidth > 0) {
      image.classList.add("loaded");
      loading.classList.add("hidden");
    }
  });
  const buttons = append
    ? Array.from(grid.querySelectorAll("[data-path]")).slice(-photos.length)
    : Array.from(grid.querySelectorAll("[data-path]"));
  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const index = state.loadedPhotos.findIndex((item) => item.path === button.dataset.path);
      if (index >= 0) openViewer(state.loadedPhotos, index, "all", state.galleryPerson?.name || state.collectionName);
    });
  });
  const selectionInputs = append
    ? Array.from(grid.querySelectorAll("[data-select-path]")).slice(-photos.length)
    : Array.from(grid.querySelectorAll("[data-select-path]"));
  selectionInputs.forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) state.selectedPhotoPaths.add(input.dataset.selectPath);
      else state.selectedPhotoPaths.delete(input.dataset.selectPath);
      updateSelectionControls();
    });
  });
  updateSelectionControls();
}

function updateSelectionControls() {
  const selectedCount = state.selectedPhotoPaths.size;
  $("#selection-count").textContent = `${selectedCount} selected`;
  $("#download-selected").disabled = selectedCount === 0 || state.downloadBusy;
  $("#download-selected-zip").disabled = selectedCount === 0 || state.downloadBusy;
  $("#photo-grid").querySelectorAll(".photo-tile.selectable").forEach((tile) => {
    const input = tile.querySelector("[data-select-path]");
    input.checked = state.selectedPhotoPaths.has(input.dataset.selectPath);
    tile.classList.toggle("selected", input.checked);
  });
}

async function loadPhotos({ reset = false, query = state.photoQuery, kind = state.photoFilter, sort = state.photoSort } = {}) {
  if (state.photoLoading && !reset) return;
  if (!reset && state.loadedPhotos.length >= state.photoTotal) return;
  if (reset) {
    state.photoRequestId += 1;
    state.photoPage = 0;
    state.photoTotal = 0;
    state.loadedPhotos = [];
    state.photoQuery = query;
    state.photoFilter = kind;
    state.photoSort = sort;
    state.photoLoading = false;
    $("#photo-grid").replaceChildren();
    $("#all-empty").classList.add("hidden");
    $("#all-end").classList.add("hidden");
    $("#all-loading").classList.remove("hidden");
    $("#all-loading-more").classList.add("hidden");
  }
  state.photoLoading = true;
  const requestId = ++state.photoRequestId;
  const page = state.photoPage + 1;
  if (page > 1) {
    $("#all-loading-more").classList.remove("hidden");
    $("#all-more-spinner").classList.remove("hidden");
    $("#all-more-text").textContent = "Loading more photos…";
  }
  try {
    const result = await requestJson(`/api/photos?${apiQuery({
      page: String(page),
      page_size: String(state.photoPageSize),
      q: query,
      kind,
      sort,
    })}`);
    if (requestId !== state.photoRequestId) return;
    state.photoTotal = result.total;
    $("#all-count").textContent = `(${result.total.toLocaleString()})`;
    const labels = {
      all: query ? `Photos matching “${query}”.` : "Every photo in your collection, in one place.",
      liked: "Photos you liked.",
      disliked: "Photos you disliked.",
      deleted: "Photos in the recycle bin.",
    };
    $("#all-subtitle").textContent = labels[kind] || labels.all;
    state.loadedPhotos.push(...result.photos);
    state.photoPage = page;
    renderPhotoGrid(result.photos, page > 1);
    $("#all-empty").textContent = query
      ? "No indexed photo paths matched. Try another name, place, or moment."
      : kind === "liked"
        ? "No liked photos yet. Tap the heart on a photo to save it here."
        : kind === "disliked"
          ? "No disliked photos yet. Use the thumbs down on a photo to save it here."
          : kind === "deleted"
            ? "No photos are in the recycle bin yet."
            : "No photos found. Index a folder from Collection to get started.";
    $("#photo-grid").classList.toggle("hidden", state.loadedPhotos.length === 0);
    $("#all-empty").classList.toggle("hidden", state.loadedPhotos.length !== 0);
    const finished = state.loadedPhotos.length >= result.total || result.photos.length === 0;
    $("#all-end").classList.toggle("hidden", !finished || result.total === 0);
    $("#all-loading-more").classList.toggle("hidden", finished);
    $("#all-more-spinner").classList.add("hidden");
    $("#all-more-text").textContent = "Scroll to load more";
  } catch (error) {
    if (requestId !== state.photoRequestId) return;
    showNotice($("#home-message"), error.message, "error");
    $("#all-empty").textContent = error.message;
    if (!state.loadedPhotos.length) $("#all-empty").classList.remove("hidden");
    else {
      $("#all-loading-more").classList.remove("hidden");
      $("#all-more-spinner").classList.add("hidden");
      $("#all-more-text").textContent = "Could not load more. Scroll to retry.";
    }
  } finally {
    if (requestId === state.photoRequestId) {
      state.photoLoading = false;
      $("#all-loading").classList.add("hidden");
      $("#all-more-spinner").classList.add("hidden");
      if (state.loadedPhotos.length < state.photoTotal) {
        $("#all-loading-more").classList.remove("hidden");
        if ($("#all-more-text").textContent === "Loading more photos…") {
          $("#all-more-text").textContent = "Scroll to load more";
        }
      }
    }
  }
}

async function openAll(query = "", kind = state.photoFilter) {
  state.galleryPerson = null;
  state.selectedPhotoPaths.clear();
  $("#all-title").textContent = "All photos";
  $("#all-back").textContent = "← Home";
  $("#all-refresh").classList.remove("hidden");
  $("#photo-filters").classList.remove("hidden");
  $("#selection-tools").classList.add("hidden");
  hideNotice($("#gallery-message"));
  state.returnScreen = "all";
  state.photoFilter = kind;
  document.querySelectorAll(".filter-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.photoFilter === kind);
  });
  setScreen("all");
  await loadPhotos({ reset: true, query, kind, sort: state.photoSort });
}

const photoScrollObserver = new IntersectionObserver((entries) => {
  if (entries.some((entry) => entry.isIntersecting)
    && state.screen === "all"
    && !state.photoLoading
    && state.loadedPhotos.length < state.photoTotal) {
    loadPhotos();
  }
}, { rootMargin: "320px 0px" });
photoScrollObserver.observe($("#all-loading-more"));

const peopleScrollObserver = new IntersectionObserver((entries) => {
  if (entries.some((entry) => entry.isIntersecting)
    && state.screen === "collection"
    && !state.peopleLoading
    && state.loadedPeople.length < state.peopleTotal) {
    loadPeople();
  }
}, { rootMargin: "320px 0px" });
peopleScrollObserver.observe($("#people-loading-more"));

function renderPeople(people, append = false) {
  const container = $("#people-grid");
  const markup = people.map((person) => `
    <article class="person-card">
      <button class="person-open" data-person="${escapeHtml(person.key)}" aria-label="Browse photos of ${escapeHtml(person.name)}">
        <span class="person-avatar">
          <img loading="lazy" src="${faceUrl(person.preview)}" alt="">
          <span class="avatar-loading"><span class="spinner"></span></span>
        </span>
      </button>
      <div class="person-label">
        <input type="text" maxlength="80" value="${escapeHtml(person.name)}" aria-label="Person name">
        <button data-save-person="${escapeHtml(person.key)}" title="Save name" aria-label="Save person name">✎</button>
      </div>
      <div class="person-actions">
        <button class="person-preview-button" data-preview-person="${escapeHtml(person.key)}" type="button">Open gallery</button>
      </div>
      <span class="person-count">${person.count} photo${person.count === 1 ? "" : "s"}</span>
    </article>`).join("");
  if (append) {
    container.insertAdjacentHTML("beforeend", markup);
  } else {
    container.innerHTML = markup;
  }
  const buttons = append
    ? Array.from(container.querySelectorAll("[data-person]")).slice(-people.length)
    : Array.from(container.querySelectorAll("[data-person]"));
  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const person = state.loadedPeople.find((item) => item.key === button.dataset.person)
        || state.people.find((item) => item.key === button.dataset.person);
      if (person) openPersonGallery(person);
    });
  });
  container.querySelectorAll("[data-preview-person]").forEach((button) => {
    button.addEventListener("click", () => {
      const person = state.loadedPeople.find((item) => item.key === button.dataset.previewPerson)
        || state.people.find((item) => item.key === button.dataset.previewPerson);
      if (person) openPersonGallery(person);
    });
  });
  container.querySelectorAll(".person-avatar img").forEach((image) => {
    const loading = image.parentElement.querySelector(".avatar-loading");
    image.addEventListener("load", () => {
      image.classList.add("loaded");
      loading.classList.add("hidden");
    }, { once: true });
    image.addEventListener("error", () => loading.classList.add("hidden"), { once: true });
    if (image.complete && image.naturalWidth > 0) {
      image.classList.add("loaded");
      loading.classList.add("hidden");
    }
  });
  container.querySelectorAll("[data-save-person]").forEach((button) => {
    const input = button.parentElement.querySelector("input");
    const save = () => savePersonName(button.dataset.savePerson, input.value, input);
    button.addEventListener("click", save);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        input.blur();
      }
    });
    input.addEventListener("change", save);
  });
  $("#people-grid").classList.toggle("hidden", state.loadedPeople.length === 0);
  $("#people-empty").classList.toggle("hidden", state.loadedPeople.length !== 0);
  $("#people-subtitle").textContent = state.loadedPeople.length
    ? `${state.loadedPeople.length} face groups found in your photos. Tap a person to browse their photos.`
    : "Faces found in your photos, brought together.";
}

function openPersonGallery(person) {
  state.galleryPerson = person;
  state.selectedPhotoPaths.clear();
  state.loadedPhotos = person.photos.map((photo) => ({ ...photo, faces: 1 }));
  state.photoTotal = state.loadedPhotos.length;
  state.photoQuery = "";
  state.photoFilter = "all";
  state.photoLoading = false;
  state.photoRequestId += 1;
  state.returnScreen = "all";
  $("#all-title").textContent = person.name;
  $("#all-count").textContent = `(${person.photos.length.toLocaleString()})`;
  $("#all-subtitle").textContent = `Photos grouped with ${person.name}. Select any number to download.`;
  $("#all-back").textContent = "← Collection";
  $("#all-refresh").classList.add("hidden");
  $("#photo-filters").classList.add("hidden");
  $("#selection-tools").classList.remove("hidden");
  hideNotice($("#gallery-message"));
  $("#all-loading").classList.add("hidden");
  $("#all-loading-more").classList.add("hidden");
  $("#all-end").classList.add("hidden");
  $("#photo-grid").classList.toggle("hidden", state.loadedPhotos.length === 0);
  $("#all-empty").classList.toggle("hidden", state.loadedPhotos.length !== 0);
  $("#all-empty").textContent = "This person has no photos in the current index.";
  renderPhotoGrid(state.loadedPhotos);
  setScreen("all");
}

async function loadPeople({ reset = false, query = state.peopleQuery } = {}) {
  if (state.peopleLoading && !reset) return;
  if (!reset && state.loadedPeople.length >= state.peopleTotal) return;
  if (reset) {
    state.peopleRequestId += 1;
    state.peoplePage = 1;
    state.peopleQuery = query;
    state.peopleTotal = 0;
    state.loadedPeople = [];
    state.people = [];
    state.peopleLoading = false;
    $("#people-grid").replaceChildren();
    $("#people-empty").classList.add("hidden");
    $("#people-end").classList.add("hidden");
    $("#people-loading").classList.remove("hidden");
    $("#people-loading-more").classList.add("hidden");
  }
  state.peopleLoading = true;
  const requestId = ++state.peopleRequestId;
  const page = state.peoplePage;
  $("#people-loading-more").classList.remove("hidden");
  $("#people-more-spinner").classList.remove("hidden");
  $("#people-more-text").textContent = "Loading more people…";
  try {
    const result = await requestJson(`/api/people?${apiQuery({
      page: String(page),
      page_size: String(state.peoplePageSize),
      q: query,
    })}`);
    if (requestId !== state.peopleRequestId) return;
    state.peopleTotal = result.total;
    state.loadedPeople.push(...result.people);
    state.people = state.loadedPeople;
    renderPeople(result.people, page > 1);
    if (state.peopleQuery) {
      $("#people-subtitle").textContent =
        `${result.total} collection name${result.total === 1 ? "" : "s"} match “${state.peopleQuery}”.`;
    }
    const finished = state.loadedPeople.length >= result.total || result.people.length === 0;
    $("#people-end").classList.toggle("hidden", !finished || result.total === 0);
    $("#people-loading-more").classList.toggle("hidden", finished);
    $("#people-more-spinner").classList.add("hidden");
    $("#people-more-text").textContent = "Scroll to load more";
    if (!finished) state.peoplePage += 1;
  } catch (error) {
    if (requestId !== state.peopleRequestId) return;
    $("#people-empty").textContent = error.message;
    $("#people-empty").classList.remove("hidden");
    $("#people-loading-more").classList.remove("hidden");
    $("#people-more-spinner").classList.add("hidden");
    $("#people-more-text").textContent = "Could not load more. Scroll to retry.";
  } finally {
    if (requestId === state.peopleRequestId) {
      state.peopleLoading = false;
      $("#people-loading").classList.add("hidden");
      $("#people-more-spinner").classList.add("hidden");
    }
  }
}

async function openCollection() {
  state.peopleQuery = "";
  state.returnScreen = "collection";
  setScreen("collection");
  await Promise.all([loadPeople({ reset: true }), loadCollectionTitle()]);
}

async function loadCollectionTitle() {
  try {
    const result = await requestJson(`/api/collection-name?${apiQuery()}`);
    state.collectionName = result.name;
    $("#collection-name").value = result.name;
    $("#viewer-title").value = result.name;
  } catch (error) {
    showNotice($("#index-message"), error.message, "error");
  }
}

async function savePersonName(key, name, input) {
  try {
    const result = await requestJson("/api/person-name", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ database: state.database, key, name }),
    });
    input.value = result.name;
    showNotice($("#index-message"), `Saved person name: ${result.name}`, "success");
  } catch (error) {
    showNotice($("#index-message"), error.message, "error");
  }
}

async function saveCollectionName(name) {
  try {
    const result = await requestJson("/api/collection-name", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ database: state.database, name }),
    });
    state.collectionName = result.name;
    $("#collection-name").value = result.name;
    $("#viewer-title").value = result.name;
    showNotice($("#index-message"), "Collection name saved.", "success");
  } catch (error) {
    showNotice($("#index-message"), error.message, "error");
  }
}

function openViewer(photos, index, origin, title = state.collectionName) {
  if (!photos.length) return;
  state.viewerPhotos = photos;
  state.viewerIndex = index;
  state.returnScreen = origin;
  $("#viewer-title").value = title;
  $("#viewer-title").readOnly = true;
  $("#edit-title").textContent = "✎";
  setScreen("viewer");
  renderViewer();
}

function renderViewer() {
  if (!state.viewerPhotos.length) return;
  const photo = state.viewerPhotos[state.viewerIndex];
  const previous = state.viewerPhotos[(state.viewerIndex - 1 + state.viewerPhotos.length) % state.viewerPhotos.length];
  const next = state.viewerPhotos[(state.viewerIndex + 1) % state.viewerPhotos.length];
  const hasNeighbors = state.viewerPhotos.length > 1;
  const mainImage = $("#main-photo");
  const loading = $(".viewer-loading");
  mainImage.classList.remove("loaded");
  loading.classList.remove("hidden");
  mainImage.src = imageUrl(photo.path);
  mainImage.alt = photo.path.split(/[\\/]/).pop();
  mainImage.onload = () => {
    mainImage.classList.add("loaded");
    loading.classList.add("hidden");
  };
  mainImage.onerror = () => {
    loading.classList.add("hidden");
    showNotice($("#viewer-message"), "Could not load this photo.", "error");
  };
  $("#viewer-position").textContent = `${state.viewerIndex + 1} / ${state.viewerPhotos.length}`;
  $("#photo-filename").textContent = photo.path.split(/[\\/]/).pop();
  $("#photo-filename").title = photo.path;
  $("#photo-subtitle").textContent = photo.faces ? `${photo.faces} detected face${photo.faces === 1 ? "" : "s"}` : "";
  $("#viewer-previous").disabled = !hasNeighbors;
  $("#viewer-next").disabled = !hasNeighbors;
  $("#viewer-prev-arrow").disabled = !hasNeighbors;
  $("#viewer-next-arrow").disabled = !hasNeighbors;
  $("#viewer-previous img").src = imageUrl(previous.path);
  $("#viewer-next img").src = imageUrl(next.path);
  const feedback = state.viewerFeedback[photo.path];
  $("#like-button").classList.toggle("selected", feedback === "like");
  $("#like-button").textContent = feedback === "like" ? "♥" : "♡";
  $("#approve-button").classList.toggle("selected", feedback === "up");
  $("#dislike-button").classList.toggle("selected", feedback === "down");
}

function stepViewer(delta) {
  if (!state.viewerPhotos.length) return;
  const total = state.viewerPhotos.length;
  state.viewerIndex = (state.viewerIndex + delta + total) % total;
  renderViewer();
}

function previewCurrentPhoto() {
  if (!state.viewerPhotos.length) return;
  const photo = state.viewerPhotos[state.viewerIndex];
  const preview = $("#photo-preview-modal");
  const image = $("#photo-preview-image");
  image.alt = photo.path.split(/[\\/]/).pop() || "Full-size photo preview";
  image.src = imageUrl(photo.path);
  preview.classList.remove("hidden");
  $("#photo-preview-close").focus();
}

function closePhotoPreview() {
  const preview = $("#photo-preview-modal");
  if (preview.classList.contains("hidden")) return;
  preview.classList.add("hidden");
  $("#photo-preview-image").removeAttribute("src");
  $("#preview-photo").focus();
}

function downloadCurrentPhoto() {
  if (!state.viewerPhotos.length) return;
  const photo = state.viewerPhotos[state.viewerIndex];
  const link = document.createElement("a");
  const filename = photo.path.split(/[\\/]/).pop() || "photo";
  link.href = imageUrl(photo.path);
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function relativePhotoPath(path) {
  const normalizedPath = path.replace(/\\/g, "/");
  const normalizedRoot = state.collectionPath.replace(/\\/g, "/").replace(/\/+$/, "");
  const relative = normalizedPath.toLowerCase().startsWith(`${normalizedRoot.toLowerCase()}/`)
    ? normalizedPath.slice(normalizedRoot.length + 1)
    : normalizedPath;
  const segments = relative.split("/").filter((part) => part && part !== "." && part !== "..");
  return segments.length ? segments : [normalizedPath.split("/").pop() || "photo"];
}

async function downloadToDirectory(directory, photos) {
  for (let index = 0; index < photos.length; index += 1) {
    const photo = photos[index];
    showNotice($("#gallery-message"), `Saving photo ${index + 1} of ${photos.length}…`, "info");
    const response = await fetch(imageUrl(photo.path));
    if (!response.ok) throw new Error(`Could not download ${photo.path} (${response.status}).`);
    const blob = await response.blob();
    const segments = relativePhotoPath(photo.path);
    let target = directory;
    for (const segment of segments.slice(0, -1)) {
      target = await target.getDirectoryHandle(segment, { create: true });
    }
    const file = await target.getFileHandle(segments[segments.length - 1], { create: true });
    const writable = await file.createWritable();
    try {
      await writable.write(blob);
      await writable.close();
    } catch (error) {
      await writable.abort();
      throw error;
    }
  }
}

async function downloadSelectedPhotos() {
  if (state.downloadBusy) return;
  const photos = state.loadedPhotos.filter((photo) => state.selectedPhotoPaths.has(photo.path));
  if (!photos.length) return;
  state.downloadBusy = true;
  updateSelectionControls();
  try {
    if (typeof window.showDirectoryPicker === "function") {
      let directory;
      showNotice($("#gallery-message"), "Choose a destination folder in the folder picker…", "info");
      try {
        directory = await window.showDirectoryPicker({ mode: "readwrite" });
      } catch (error) {
        if (error.name === "AbortError") return;
        throw error;
      }
      await downloadToDirectory(directory, photos);
      showNotice($("#gallery-message"), `Saved ${photos.length} photo${photos.length === 1 ? "" : "s"} to the selected folder.`, "success");
      return;
    }

    await downloadSelectedAsZip(photos);
  } catch (error) {
    showNotice($("#gallery-message"), error.message, "error");
  } finally {
    state.downloadBusy = false;
    updateSelectionControls();
  }
}

async function downloadSelectedAsZip(photos) {
  showNotice($("#gallery-message"), "Preparing your ZIP download…", "info");
  const response = await fetch("/api/download-zip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      database: state.database,
      paths: photos.map((photo) => photo.path),
    }),
  });
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || `Download failed (${response.status}).`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `photos-${(state.galleryPerson?.name || "selected").replace(/[^a-z0-9-_]+/gi, "-")}.zip`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  showNotice($("#gallery-message"), `Downloaded ${photos.length} photo${photos.length === 1 ? "" : "s"} as a ZIP file.`, "success");
}

async function downloadSelectedPhotosAsZip() {
  if (state.downloadBusy) return;
  const photos = state.loadedPhotos.filter((photo) => state.selectedPhotoPaths.has(photo.path));
  if (!photos.length) return;
  state.downloadBusy = true;
  updateSelectionControls();
  try {
    await downloadSelectedAsZip(photos);
  } catch (error) {
    showNotice($("#gallery-message"), error.message, "error");
  } finally {
    state.downloadBusy = false;
    updateSelectionControls();
  }
}

async function deleteCurrentPhoto() {
  if (!state.viewerPhotos.length) return;
  const photo = state.viewerPhotos[state.viewerIndex];
  const confirmed = window.confirm(`Delete this photo from your collection?\n\n${photo.path}\n\nThis action cannot be undone.`);
  if (!confirmed) return;
  try {
    const result = await requestJson("/api/delete-photo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ database: state.database, path: photo.path }),
    });
    state.viewerPhotos = state.viewerPhotos.filter((item) => item.path !== photo.path);
    state.loadedPhotos = state.loadedPhotos.filter((item) => item.path !== photo.path);
    state.photoTotal = Math.max(0, state.photoTotal - 1);
    if (!state.viewerPhotos.length) {
      state.viewerIndex = 0;
      showNotice($("#viewer-message"), result.message || "Photo deleted.", "success");
      setScreen(state.returnScreen || "home");
      return;
    }
    state.viewerIndex = Math.min(state.viewerIndex, state.viewerPhotos.length - 1);
    renderViewer();
    showNotice($("#viewer-message"), result.message || "Photo deleted.", "success");
  } catch (error) {
    showNotice($("#viewer-message"), error.message, "error");
  }
}

async function saveFeedback(value) {
  const photo = state.viewerPhotos[state.viewerIndex];
  if (!photo) return;
  const oldValue = state.viewerFeedback[photo.path] || null;
  const nextValue = oldValue === value ? null : value;
  state.viewerFeedback[photo.path] = nextValue;
  renderViewer();
  if (value === "like" && nextValue) animateHeart();
  try {
    await requestJson("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ database: state.database, path: photo.path, value: nextValue }),
    });
  } catch (error) {
    if (oldValue) state.viewerFeedback[photo.path] = oldValue;
    else delete state.viewerFeedback[photo.path];
    renderViewer();
    showNotice($("#viewer-message"), error.message, "error");
  }
}

function animateHeart() {
  const heart = $("#like-heart");
  heart.classList.remove("hidden");
  heart.style.animation = "none";
  void heart.offsetWidth;
  heart.style.animation = "";
  window.setTimeout(() => heart.classList.add("hidden"), 700);
}

function toggleLike() {
  saveFeedback("like");
}

async function searchByText(query) {
  const term = query.trim();
  if (!term) {
    await openAll();
    return;
  }
  try {
    const result = await requestJson(`/api/people?${apiQuery({
      page: "1",
      page_size: String(state.peoplePageSize),
      q: term,
    })}`);
    const normalizedTerm = term.toLocaleLowerCase();
    const matchingPerson = result.people.find((person) =>
      person.name.toLocaleLowerCase() === normalizedTerm)
      || (result.total === 1 ? result.people[0] : null);
    if (matchingPerson) {
      openPersonGallery(matchingPerson);
      return;
    }
    if (result.total > 1) {
      state.peopleRequestId += 1;
      state.peopleQuery = term;
      state.peoplePage = 2;
      state.peopleTotal = result.total;
      state.loadedPeople = result.people;
      state.people = state.loadedPeople;
      state.peopleLoading = false;
      $("#people-loading").classList.add("hidden");
      $("#people-more-spinner").classList.add("hidden");
      renderPeople(result.people);
      $("#people-subtitle").textContent =
        `${result.total} collection names match “${term}”.`;
      $("#people-end").classList.toggle("hidden", state.loadedPeople.length < result.total);
      $("#people-loading-more").classList.toggle("hidden", state.loadedPeople.length >= result.total);
      state.returnScreen = "collection";
      setScreen("collection");
      await loadCollectionTitle();
      return;
    }
  } catch (error) {
    showNotice($("#home-message"), error.message, "error");
    return;
  }
  await openAll(term);
}

async function readImageBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result);
      resolve(value.slice(value.indexOf(",") + 1));
    };
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}

function chooseFace(faces) {
  if (faces.length === 1) return Promise.resolve(faces[0].index);
  return new Promise((resolve) => {
    const modal = $("#face-picker-modal");
    const choices = $("#face-choice-list");
    const finish = (index) => {
      modal.classList.add("hidden");
      choices.replaceChildren();
      resolve(index);
    };
    choices.innerHTML = faces.map((face, index) => {
      const box = face.bbox;
      return `<button type="button" data-face-index="${face.index}">
        Face ${index + 1}<br><small>Position ${box.x1}, ${box.y1}</small>
      </button>`;
    }).join("");
    choices.querySelectorAll("[data-face-index]").forEach((button) =>
      button.addEventListener("click", () => finish(Number(button.dataset.faceIndex))));
    modal.classList.remove("hidden");
    $("#face-picker-close").onclick = () => finish(null);
  });
}

async function searchByImage(file) {
  return searchByImages([file]);
}

async function searchByImages(files) {
  const message = $("#home-message");
  $("#search-overlay").classList.remove("hidden");
  $("#search-overlay-title").textContent = "Looking for your pic…";
  $("#search-overlay-detail").textContent = files.length > 1
    ? `Preparing ${files.length} reference photos.`
    : "Detecting a face and searching your local index.";
  showNotice(message, "Finding a face in your photo…", "info");
  try {
    const queries = [];
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      $("#search-overlay-detail").textContent = `Detecting faces in reference ${index + 1} of ${files.length}.`;
      const data = await readImageBase64(file);
      const detected = await requestJson("/api/detect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          data,
          name: file.name || "camera.jpg",
          backend: $("#backend").value || "insightface",
        }),
      });
      const faceIndex = await chooseFace(detected.faces);
      if (faceIndex === null) throw new Error("Search cancelled.");
      queries.push({ token: detected.token, face_index: faceIndex });
    }
    $("#search-overlay-title").textContent = "Matching your photo…";
    $("#search-overlay-detail").textContent = "Comparing face embeddings in your local collection.";
    const result = await requestJson("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        database: state.database,
        queries,
        threshold: Number($("#threshold").value),
        top_n: Number($("#top-n").value),
      }),
    });
    const photos = result.matches.map((match) => ({ path: match.path, faces: 1, score: match.score }));
    hideNotice(message);
    showNotice(message, `${photos.length} matching photo${photos.length === 1 ? "" : "s"} found.`, "success");
    if (photos.length) openViewer(photos, 0, "home", state.collectionName);
  } catch (error) {
    if (error.message !== "Search cancelled.") showNotice(message, error.message, "error");
  } finally {
    $("#face-picker-modal").classList.add("hidden");
    $("#search-overlay").classList.add("hidden");
  }
}

async function startCamera() {
  const modal = $("#camera-modal");
  modal.classList.remove("hidden");
  $("#camera-fallback").classList.add("hidden");
  try {
    state.cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user" }, audio: false,
    });
    $("#camera-video").srcObject = state.cameraStream;
    $("#capture-photo").disabled = false;
  } catch (error) {
    $("#camera-fallback").classList.remove("hidden");
    $("#camera-fallback").textContent = `Camera unavailable: ${error.message}`;
    $("#capture-photo").disabled = true;
  }
}

function stopCamera() {
  if (state.cameraStream) {
    state.cameraStream.getTracks().forEach((track) => track.stop());
    state.cameraStream = null;
  }
  $("#camera-video").srcObject = null;
}

async function capturePhoto() {
  const video = $("#camera-video");
  if (!video.videoWidth || !video.videoHeight) return;
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob) {
    showNotice($("#home-message"), "Could not capture a photo.", "error");
    return;
  }
  $("#camera-modal").classList.add("hidden");
  stopCamera();
  await searchByImage(new File([blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" }));
}

$("#text-search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = $("#text-search").value.trim();
  if (query) await searchByText(query);
  else $("#text-search").focus();
});
$("#upload-trigger").addEventListener("click", () => $("#photo-upload").click());
$("#photo-upload").addEventListener("change", async (event) => {
  const files = Array.from(event.target.files || []);
  event.target.value = "";
  if (files.length) await searchByImages(files);
});
$("#open-camera").addEventListener("click", startCamera);
$("#camera-close").addEventListener("click", () => {
  $("#camera-modal").classList.add("hidden");
  stopCamera();
});
$("#capture-photo").addEventListener("click", capturePhoto);
$("#threshold").addEventListener("input", (event) => {
  $("#threshold-value").value = Number(event.target.value).toFixed(2);
});
$("#all-refresh").addEventListener("click", () =>
  loadPhotos({ reset: true, query: state.photoQuery, kind: state.photoFilter, sort: state.photoSort }));
$("#photo-sort").addEventListener("change", (event) => {
  state.photoSort = event.target.value;
  loadPhotos({ reset: true, query: state.photoQuery, kind: state.photoFilter, sort: state.photoSort });
});
document.querySelectorAll(".filter-button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter-button").forEach((item) => item.classList.toggle("active", item === button));
    state.photoFilter = button.dataset.photoFilter;
    loadPhotos({ reset: true, query: state.photoQuery, kind: state.photoFilter, sort: state.photoSort });
  });
});
$("#people-refresh").addEventListener("click", loadPeople);
$("#select-all-photos").addEventListener("click", () => {
  if (!state.galleryPerson) return;
  state.loadedPhotos.forEach((photo) => state.selectedPhotoPaths.add(photo.path));
  updateSelectionControls();
});
$("#clear-photo-selection").addEventListener("click", () => {
  state.selectedPhotoPaths.clear();
  updateSelectionControls();
});
$("#download-selected").addEventListener("click", downloadSelectedPhotos);
$("#download-selected-zip").addEventListener("click", downloadSelectedPhotosAsZip);
$("#all-back").addEventListener("click", goBack);
document.querySelectorAll("[data-open-photo-filter]").forEach((button) => {
  button.addEventListener("click", () => openAll("", button.dataset.openPhotoFilter));
});
$("#save-collection-name").addEventListener("click", () => saveCollectionName($("#collection-name").value));
$("#collection-name").addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveCollectionName(event.currentTarget.value);
});
$("#viewer-back").addEventListener("click", goBack);
$("#preview-photo").addEventListener("click", previewCurrentPhoto);
$("#photo-preview-close").addEventListener("click", closePhotoPreview);
$("#photo-preview-modal").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closePhotoPreview();
});
$("#photo-preview-modal").addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closePhotoPreview();
  }
});
$("#download-photo").addEventListener("click", downloadCurrentPhoto);
$("#delete-photo").addEventListener("click", deleteCurrentPhoto);
$("#edit-title").addEventListener("click", () => {
  const input = $("#viewer-title");
  input.readOnly = !input.readOnly;
  if (input.readOnly) {
    $("#edit-title").textContent = "✎";
    saveCollectionName(input.value);
  } else {
    $("#edit-title").textContent = "✓";
    input.focus();
    input.select();
  }
});
$("#viewer-title").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    $("#viewer-title").readOnly = true;
    $("#edit-title").textContent = "✎";
    saveCollectionName(event.currentTarget.value);
  }
  if (event.key === "Escape") {
    event.currentTarget.value = state.collectionName;
    event.currentTarget.readOnly = true;
    $("#edit-title").textContent = "✎";
  }
});
$("#viewer-previous").addEventListener("click", () => stepViewer(-1));
$("#viewer-prev-arrow").addEventListener("click", () => stepViewer(-1));
$("#viewer-next").addEventListener("click", () => stepViewer(1));
$("#viewer-next-arrow").addEventListener("click", () => stepViewer(1));
$("#like-button").addEventListener("click", toggleLike);
$("#approve-button").addEventListener("click", () => saveFeedback("up"));
$("#dislike-button").addEventListener("click", () => saveFeedback("down"));
$("#main-photo-wrap").addEventListener("dblclick", toggleLike);
$("#main-photo-wrap").addEventListener("touchend", (event) => {
  const now = Date.now();
  if (now - state.lastTap < 320) {
    event.preventDefault();
    toggleLike();
  }
  state.lastTap = now;
});

document.querySelectorAll("[data-screen]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.screen;
    if (target === "collection") openCollection();
    else if (target === "all") openAll();
    else setScreen("home");
  });
});

$("#index-button").addEventListener("click", async () => {
  if (state.indexBusy) return;
  state.indexBusy = true;
  const button = $("#index-button");
  button.disabled = true;
  $("#index-progress").classList.remove("hidden");
  hideNotice($("#index-message"));
  try {
    const result = await requestJson("/api/index", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        database: $("#database").value.trim(),
        collection: $("#collection-path").value.trim(),
        backend: $("#backend").value,
        workers: Number($("#workers").value),
      }),
    });
    state.database = $("#database").value.trim();
    state.collectionPath = $("#collection-path").value.trim();
    const report = result.report;
    showNotice(
      $("#index-message"),
      `Scanned ${report.scanned}; indexed ${report.indexed} files and ${report.face_count} faces; skipped ${report.skipped}, removed ${report.removed}, failed ${report.failed}.`,
      report.failed ? "error" : "success",
    );
    if (report.errors.length) {
      $("#index-message").textContent += ` ${report.errors.slice(0, 3).join(" · ")}`;
    }
    await Promise.all([
      loadPeople(),
      loadPhotos({ reset: true, query: state.photoQuery }),
      loadCollectionTitle(),
    ]);
  } catch (error) {
    showNotice($("#index-message"), error.message, "error");
  } finally {
    state.indexBusy = false;
    button.disabled = false;
    $("#index-progress").classList.add("hidden");
  }
});

$("#viewer-stage").addEventListener("touchstart", (event) => {
  state.touchStartX = event.changedTouches[0].clientX;
}, { passive: true });
$("#viewer-stage").addEventListener("touchend", (event) => {
  if (state.touchStartX === undefined) return;
  const delta = event.changedTouches[0].clientX - state.touchStartX;
  if (Math.abs(delta) > 55) stepViewer(delta < 0 ? 1 : -1);
  state.touchStartX = undefined;
}, { passive: true });
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#photo-preview-modal").classList.contains("hidden")) {
    closePhotoPreview();
    return;
  }
  if ($("#screen-viewer").classList.contains("hidden") || !$("#viewer-title").readOnly) return;
  if (event.key === "ArrowLeft") stepViewer(-1);
  if (event.key === "ArrowRight") stepViewer(1);
  if (event.key === "Escape") goBack();
});
window.addEventListener("beforeunload", stopCamera);

initialize();
