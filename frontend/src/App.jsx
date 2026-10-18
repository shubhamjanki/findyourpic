import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { downloadZip, faceUrl, imageUrl, requestJson } from "./api.js";
import LazyImage from "./LazyImage.jsx";
import Pagination from "./Pagination.jsx";

function Notice({ message, kind }) {
  if (!message) return null;
  return <div className={`notice ${kind || "info"}`} role="status">{message}</div>;
}

function formatTimeRemaining(seconds) {
  const remaining = Math.max(1, Math.ceil(seconds));
  if (remaining < 60) return `About ${remaining}s left`;
  const minutes = Math.floor(remaining / 60);
  const hours = Math.floor(minutes / 60);
  if (hours) return `About ${hours}h ${minutes % 60}m left`;
  return `About ${minutes}m left`;
}

function IndexProgress({ progress }) {
  const { phase, completed, total, phaseStartedAt } = progress;
  const phaseName = phase === "indexing" ? "Indexing faces" : "Checking photo files";
  const percentage = total > 0 ? Math.floor((completed / total) * 100) : 0;
  const elapsedSeconds = (Date.now() - phaseStartedAt) / 1000;
  const remaining = completed >= 2 && completed < total
    ? formatTimeRemaining((elapsedSeconds / completed) * (total - completed))
    : "Estimating time left";

  return (
    <div className="index-progress" role="status" aria-live="polite" aria-busy="true">
      <div className="index-progress-heading">
        <span>{phaseName}{total > 0 ? `: ${completed} of ${total} (${percentage}%)` : "…"}</span>
        <span>{remaining}</span>
      </div>
      <progress
        className="index-progress-bar"
        max={total > 0 ? total : 1}
        value={total > 0 ? completed : undefined}
        aria-label={`${phaseName} progress`}
      />
    </div>
  );
}

function fileBase(path) {
  return String(path).split(/[\\/]/).pop();
}

function readImageBase64(file) {
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

function useReveal() {
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const blocks = document.querySelectorAll("[data-reveal]");
    if (reduce || !("IntersectionObserver" in window)) {
      blocks.forEach((el) => el.classList.add("is-in"));
    } else {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            observer.unobserve(entry.target);
          }
        });
      }, { threshold: 0.1 });
      blocks.forEach((el) => observer.observe(el));
      return () => observer.disconnect();
    }
    return undefined;
  }, []);
}

function Tagline() {
  const ref = useRef(null);
  useEffect(() => {
    const tagline = ref.current;
    if (!tagline) return undefined;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const words = tagline.getAttribute("data-text").trim().split(/\s+/);
    tagline.setAttribute("aria-label", words.join(" "));
    tagline.textContent = "";
    const spans = words.map((word, i) => {
      const span = document.createElement("span");
      span.className = "word";
      span.setAttribute("aria-hidden", "true");
      span.textContent = word;
      tagline.appendChild(span);
      if (i < words.length - 1) tagline.appendChild(document.createTextNode(" "));
      return span;
    });
    if (reduce || !("IntersectionObserver" in window)) {
      spans.forEach((s) => s.classList.add("on"));
      return undefined;
    }
    const wordObserver = new IntersectionObserver((entries) => {
      entries
        .filter((e) => {
          const top = e.rootBounds ? e.rootBounds.bottom : window.innerHeight;
          return e.isIntersecting || e.boundingClientRect.top < top;
        })
        .sort((a, b) => spans.indexOf(a.target) - spans.indexOf(b.target))
        .forEach((e, i) => {
          e.target.style.transitionDelay = `${i * 40}ms`;
          e.target.classList.add("on");
          wordObserver.unobserve(e.target);
        });
    }, { rootMargin: "0px 0px -35% 0px", threshold: 0 });
    spans.forEach((s) => wordObserver.observe(s));
    return () => wordObserver.disconnect();
  }, []);
  return (
    <p
      className="tagline-text"
      data-word-reveal
      ref={ref}
      data-text="Search every photo by the people and places in it. Nothing is uploaded and nothing leaves this device, so finding the right one stays private."
    >
      Search every photo by the people and places in it. Nothing is uploaded and nothing leaves this device, so finding the right one stays private.
    </p>
  );
}

export default function App() {
  const [screen, setScreen] = useState("home");
  const [returnScreen, setReturnScreen] = useState("home");
  const [config, setConfig] = useState({ database: "", collection: "", stats: {} });
  const [collectionName, setCollectionName] = useState("My collection");
  const [backend, setBackend] = useState("insightface");
  const [workers, setWorkers] = useState("1");
  const [threshold, setThreshold] = useState(0.45);
  const [topN, setTopN] = useState(50);
  const [textQuery, setTextQuery] = useState("");
  const [homeMessage, setHomeMessage] = useState(null);
  const [indexMessage, setIndexMessage] = useState(null);
  const [indexProgress, setIndexProgress] = useState(null);
  const [galleryMessage, setGalleryMessage] = useState(null);
  const [viewerMessage, setViewerMessage] = useState(null);
  const [photos, setPhotos] = useState([]);
  const [photoTotal, setPhotoTotal] = useState(0);
  const [photoPage, setPhotoPage] = useState(0);
  const [photoSource, setPhotoSource] = useState("s3");
  const [s3NextToken, setS3NextToken] = useState(null);
  const [s3HasMore, setS3HasMore] = useState(false);
  const [photoQuery, setPhotoQuery] = useState("");
  const [photoFilter, setPhotoFilter] = useState("all");
  const [photoSort, setPhotoSort] = useState("newest");
  const [photoLoading, setPhotoLoading] = useState(false);
  const [people, setPeople] = useState([]);
  const [peopleTotal, setPeopleTotal] = useState(0);
  const [peoplePage, setPeoplePage] = useState(1);
  const [peopleQuery, setPeopleQuery] = useState("");
  const [peopleLoading, setPeopleLoading] = useState(false);
  const [galleryPerson, setGalleryPerson] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [indexBusy, setIndexBusy] = useState(false);
  const [thumbBusy, setThumbBusy] = useState(false);
  const [thumbProgress, setThumbProgress] = useState(null);
  const [thumbMessage, setThumbMessage] = useState(null);
  const [trainScreen, setTrainScreen] = useState(false);
  const [viewerPhotos, setViewerPhotos] = useState([]);
  const [viewerIndex, setViewerIndex] = useState(0);
  const [feedback, setFeedback] = useState({});
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraError, setCameraError] = useState("");
  const [searchOverlay, setSearchOverlay] = useState(null);
  const [faceChoices, setFaceChoices] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [titleReadOnly, setTitleReadOnly] = useState(true);
  const [showHeart, setShowHeart] = useState(false);
  const photoRequest = useRef(0);
  const peopleRequest = useRef(0);
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const uploadRef = useRef(null);
  const loadMoreRef = useRef(null);
  const peopleMoreRef = useRef(null);
  const faceResolve = useRef(null);
  const lastTap = useRef(0);
  const touchStartX = useRef();
  const photoLoadingRef = useRef(false);
  const peopleLoadingRef = useRef(false);

  // Refs to hold current state values for stable callbacks.
  // This avoids including mutable state in useCallback deps which
  // would recreate the callbacks on every load and trigger an
  // IntersectionObserver → load → state change → re-create loop.
  const photosRef = useRef(photos);
  const photoTotalRef = useRef(photoTotal);
  const photoPageRef = useRef(photoPage);
  const photoQueryRef = useRef(photoQuery);
  const photoFilterRef = useRef(photoFilter);
  const photoSortRef = useRef(photoSort);
  const s3NextTokenRef = useRef(s3NextToken);
  const s3HasMoreRef = useRef(s3HasMore);
  const s3TokensRef = useRef([null]);
  const peopleRef = useRef(people);
  const peopleTotalRef = useRef(peopleTotal);
  const peoplePageRef = useRef(peoplePage);
  const peopleQueryRef = useRef(peopleQuery);

  // Keep refs in sync with state
  photosRef.current = photos;
  photoTotalRef.current = photoTotal;
  photoPageRef.current = photoPage;
  photoQueryRef.current = photoQuery;
  photoFilterRef.current = photoFilter;
  photoSortRef.current = photoSort;
  s3NextTokenRef.current = s3NextToken;
  s3HasMoreRef.current = s3HasMore;
  peopleRef.current = people;
  peopleTotalRef.current = peopleTotal;
  peoplePageRef.current = peoplePage;
  peopleQueryRef.current = peopleQuery;

  useReveal();

  const goScreen = (name) => {
    setScreen(name);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const loadFeedback = useCallback(async () => {
    const result = await requestJson("/api/feedback");
    setFeedback(result.feedback || {});
  }, []);

  const loadTitle = useCallback(async () => {
    const result = await requestJson("/api/collection-name");
    setCollectionName(result.name);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const cfg = await requestJson("/api/config");
        setConfig(cfg);
        if (cfg.stats?.backend && ["insightface", "dlib"].includes(cfg.stats.backend)) {
          setBackend(cfg.stats.backend);
        }
        await loadTitle();
        await loadFeedback();
      } catch (error) {
        setHomeMessage({ text: error.message, kind: "error" });
      }
    })();
  }, [loadFeedback, loadTitle]);

  const loadPhotos = useCallback(async ({ reset = false, page: targetPage, query, kind, sort } = {}) => {
    const curQuery = query !== undefined ? query : photoQueryRef.current;
    const curKind = kind !== undefined ? kind : photoFilterRef.current;
    const curSort = sort !== undefined ? sort : photoSortRef.current;

    if (photoLoadingRef.current && !reset && targetPage === undefined) return;

    const requestId = ++photoRequest.current;
    photoLoadingRef.current = true;
    setPhotoLoading(true);

    const pageToLoad = targetPage !== undefined ? targetPage : (reset ? 1 : (photoPageRef.current || 1));

    if (reset || targetPage !== undefined) {
      setPhotoPage(pageToLoad);
      photoPageRef.current = pageToLoad;
      if (reset) {
        setPhotos([]);
        setPhotoTotal(0);
        setPhotoQuery(curQuery);
        setPhotoFilter(curKind);
        setPhotoSort(curSort);
        photosRef.current = [];
        photoTotalRef.current = 0;
        photoQueryRef.current = curQuery;
        photoFilterRef.current = curKind;
        photoSortRef.current = curSort;
      }
    }
    try {
      const result = await requestJson(
        `/api/photos?page=${pageToLoad}&page_size=25&q=${encodeURIComponent(curQuery)}&kind=${encodeURIComponent(curKind)}&sort=${encodeURIComponent(curSort)}`,
      );
      if (requestId !== photoRequest.current) return;
      setPhotoTotal(result.total);
      setPhotos(result.photos);
      setPhotoPage(pageToLoad);
      photoPageRef.current = pageToLoad;
      photosRef.current = result.photos;
      photoTotalRef.current = result.total;
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      if (requestId !== photoRequest.current) return;
      setHomeMessage({ text: error.message, kind: "error" });
      setGalleryMessage({ text: error.message, kind: "error" });
    } finally {
      if (requestId === photoRequest.current) {
        photoLoadingRef.current = false;
        setPhotoLoading(false);
      }
    }
  }, []);

  const loadS3Photos = useCallback(async ({ reset = false, page: targetPage } = {}) => {
    if (photoLoadingRef.current && !reset && targetPage === undefined) return;

    const requestId = ++photoRequest.current;
    photoLoadingRef.current = true;
    setPhotoLoading(true);

    let pageToLoad = targetPage !== undefined ? targetPage : (reset ? 1 : (photoPageRef.current || 1));
    if (reset) {
      s3TokensRef.current = [null];
      pageToLoad = 1;
      setPhotoPage(1);
      photoPageRef.current = 1;
      setPhotos([]);
      photosRef.current = [];
    }

    const startAfter = s3TokensRef.current[pageToLoad - 1] || null;
    const tokenQuery = startAfter ? `&start_after=${encodeURIComponent(startAfter)}` : "";
    try {
      const result = await requestJson(`/api/s3/photos?page_size=25${tokenQuery}`);
      if (requestId !== photoRequest.current) return;
      const nextToken = result.next_token || null;
      if (nextToken) {
        s3TokensRef.current[pageToLoad] = nextToken;
      }
      setPhotos(result.photos);
      photosRef.current = result.photos;
      setPhotoPage(pageToLoad);
      photoPageRef.current = pageToLoad;
      setS3NextToken(nextToken);
      setS3HasMore(Boolean(nextToken));
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      if (requestId !== photoRequest.current) return;
      setGalleryMessage({ text: error.message, kind: "error" });
    } finally {
      if (requestId === photoRequest.current) {
        photoLoadingRef.current = false;
        setPhotoLoading(false);
      }
    }
  }, []);

  const loadPeople = useCallback(async ({ reset = false, page: targetPage, query } = {}) => {
    const curQuery = query !== undefined ? query : peopleQueryRef.current;
    const pageToLoad = targetPage !== undefined ? targetPage : (reset ? 1 : (peoplePageRef.current || 1));

    if (peopleLoadingRef.current && !reset && targetPage === undefined) return;

    const requestId = ++peopleRequest.current;
    peopleLoadingRef.current = true;
    setPeopleLoading(true);

    if (reset || targetPage !== undefined) {
      setPeoplePage(pageToLoad);
      peoplePageRef.current = pageToLoad;
      if (reset) {
        setPeople([]);
        setPeopleTotal(0);
        setPeopleQuery(curQuery);
        peopleRef.current = [];
        peopleTotalRef.current = 0;
        peopleQueryRef.current = curQuery;
      }
    }
    try {
      const result = await requestJson(
        `/api/people?page=${pageToLoad}&page_size=25&q=${encodeURIComponent(curQuery)}`,
      );
      if (requestId !== peopleRequest.current) return;
      setPeopleTotal(result.total);
      setPeople(result.people);
      setPeoplePage(pageToLoad);
      peoplePageRef.current = pageToLoad;
      peopleRef.current = result.people;
      peopleTotalRef.current = result.total;
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      if (requestId !== peopleRequest.current) return;
      setIndexMessage({ text: error.message, kind: "error" });
    } finally {
      if (requestId === peopleRequest.current) {
        peopleLoadingRef.current = false;
        setPeopleLoading(false);
      }
    }
  }, []);

  const openAll = async (query = "", kind = "all") => {
    setGalleryPerson(null);
    setPhotoSource("s3");
    setSelected(new Set());
    setReturnScreen("all");
    setGalleryMessage(null);
    goScreen("all");
    if (kind && kind !== "all") {
      setPhotoFilter(kind);
      await loadPhotos({ reset: true, query, kind, sort: photoSort });
    } else {
      await loadS3Photos({ reset: true });
    }
  };

  const openS3Gallery = async () => {
    setGalleryPerson(null);
    setPhotoSource("s3");
    setPhotoQuery("");
    setPhotoFilter("all");
    setSelected(new Set());
    setReturnScreen("all");
    setGalleryMessage(null);
    goScreen("all");
    await loadS3Photos({ reset: true });
  };

  const openCollection = async () => {
    setPeopleQuery("");
    setReturnScreen("collection");
    goScreen("collection");
    await Promise.all([loadPeople({ reset: true, query: "" }), loadTitle()]);
  };

  const openPersonGallery = (person) => {
    setGalleryPerson(person);
    setPhotoSource("s3");
    setSelected(new Set());
    setPhotos(person.photos.map((photo) => ({ ...photo, source: "s3", faces: 1 })));
    setPhotoTotal(person.photos.length);
    setPhotoQuery("");
    setPhotoFilter("all");
    setPhotoLoading(false);
    setReturnScreen("all");
    setGalleryMessage(null);
    goScreen("all");
  };

  const openViewer = (items, index, origin, title) => {
    if (!items.length) return;
    setViewerPhotos(items);
    setViewerIndex(index);
    setReturnScreen(origin);
    if (title) setCollectionName(title);
    setTitleReadOnly(true);
    setViewerMessage(null);
    goScreen("viewer");
  };

  const goBack = () => {
    if (screen === "viewer") {
      goScreen(returnScreen);
      return;
    }
    if (screen === "all" && galleryPerson) {
      openCollection();
      return;
    }
    goScreen("home");
  };



  const chooseFace = (faces, data) => {
    if (!faces || faces.length === 0) {
      throw new Error("No face was detected in this photo. Please try a different photo with a clear, visible face.");
    }
    if (faces.length === 1) return Promise.resolve(faces[0].index);
    return new Promise((resolve) => {
      faceResolve.current = resolve;
      setFaceChoices({ faces, imageSrc: data ? `data:image/jpeg;base64,${data}` : null });
    });
  };

  const searchByImages = async (files) => {
    setSearchOverlay({
      title: "Looking for your pic…",
      detail: files.length > 1 ? `Preparing ${files.length} reference photos.` : "Detecting a face and searching your local index.",
    });
    setHomeMessage({ text: "Finding a face in your photo…", kind: "info" });
    try {
      const queries = [];
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        setSearchOverlay({
          title: "Looking for your pic…",
          detail: `Detecting faces in reference ${index + 1} of ${files.length}.`,
        });
        const data = await readImageBase64(file);
        const detected = await requestJson("/api/detect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ data, name: file.name || "camera.jpg", backend }),
        });
        // Clear overlay before prompting user to choose a face if multiple are detected
        setSearchOverlay(null);
        const faceIndex = await chooseFace(detected.faces, data);
        if (faceIndex === null) throw new Error("Search cancelled.");
        if (Array.isArray(faceIndex)) {
          faceIndex.forEach((idx) => queries.push({ token: detected.token, face_index: idx }));
        } else {
          queries.push({ token: detected.token, face_index: faceIndex });
        }
      }
      setSearchOverlay({ title: "Matching your photo…", detail: "Comparing face embeddings in your collection." });
      const result = await requestJson("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ queries, threshold: Number(threshold), top_n: Number(topN) }),
      });
      const matches = result.matches.map((match) => ({
        path: match.path,
        faces: 1,
        score: match.score,
        source: match.source || (config?.storage === "s3" ? "s3" : "local"),
      }));
      if (matches.length > 0) {
        setHomeMessage({
          text: `Found ${matches.length} matching photo${matches.length === 1 ? "" : "s"}.`,
          kind: "success",
        });
        openViewer(matches, 0, "home", collectionName);
      } else {
        setHomeMessage({
          text: "No matching photos found in the index. (Try lowering the similarity threshold in Search settings below).",
          kind: "info",
        });
      }
    } catch (error) {
      if (error.message !== "Search cancelled.") setHomeMessage({ text: error.message, kind: "error" });
    } finally {
      setFaceChoices(null);
      setSearchOverlay(null);
    }
  };

  const searchByText = async (query) => {
    const term = query.trim();
    if (!term) {
      await openAll();
      return;
    }
    try {
      const result = await requestJson(`/api/people?page=1&page_size=25&q=${encodeURIComponent(term)}`);
      const normalized = term.toLocaleLowerCase();
      const matchingPerson = result.people.find((person) => person.name.toLocaleLowerCase() === normalized)
        || (result.total === 1 ? result.people[0] : null);
      if (matchingPerson) {
        openPersonGallery(matchingPerson);
        return;
      }
      if (result.total > 1) {
        setPeopleQuery(term);
        setPeople(result.people);
        setPeopleTotal(result.total);
        setPeoplePage(2);
        setReturnScreen("collection");
        goScreen("collection");
        await loadTitle();
        return;
      }
    } catch (error) {
      setHomeMessage({ text: error.message, kind: "error" });
      return;
    }
    await openAll(term);
  };

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
  };

  const startCamera = async () => {
    setCameraOpen(true);
    setCameraError("");
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("Camera API is not supported in this browser or insecure context.");
      }
      streamRef.current = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
      if (videoRef.current) {
        videoRef.current.srcObject = streamRef.current;
        videoRef.current.play().catch(() => {});
      }
    } catch (error) {
      setCameraError(`Camera unavailable: ${error.message}`);
    }
  };

  const capturePhoto = async () => {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight) {
      setCameraError("Camera stream is not ready yet. Please wait a moment.");
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) {
      setHomeMessage({ text: "Could not capture a photo.", kind: "error" });
      return;
    }
    setCameraOpen(false);
    stopCamera();
    await searchByImages([new File([blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" })]);
  };

  const saveCollectionName = async (name) => {
    try {
      const result = await requestJson("/api/collection-name", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      setCollectionName(result.name);
      setIndexMessage({ text: "Collection name saved.", kind: "success" });
    } catch (error) {
      setIndexMessage({ text: error.message, kind: "error" });
    }
  };

  const savePersonName = async (key, name) => {
    try {
      const result = await requestJson("/api/person-name", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, name }),
      });
      setPeople((current) => current.map((person) => (person.key === key ? { ...person, name: result.name } : person)));
      setIndexMessage({ text: `Saved person name: ${result.name}`, kind: "success" });
    } catch (error) {
      setIndexMessage({ text: error.message, kind: "error" });
    }
  };

  const updateIndex = async () => {
    if (indexBusy) return;
    setIndexBusy(true);
    setIndexMessage(null);
    setIndexProgress({ phase: "checking", completed: 0, total: 0, phaseStartedAt: Date.now() });
    try {
      const { job_id: jobId } = await requestJson("/api/index/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backend, workers: Number(workers) }),
      });
      let previousPhase = null;
      let phaseStartedAt = Date.now();
      let result;
      while (!result) {
        const status = await requestJson(`/api/index/status/${encodeURIComponent(jobId)}`);
        if (status.phase !== previousPhase) {
          previousPhase = status.phase;
          phaseStartedAt = Date.now();
        }
        setIndexProgress({
          phase: status.phase,
          completed: status.completed,
          total: status.total,
          phaseStartedAt,
        });
        if (status.status === "failed") throw new Error(status.error || "Indexing failed.");
        if (status.status === "completed") result = status;
        else await new Promise((resolve) => window.setTimeout(resolve, 750));
      }
      const report = result.report;
      setIndexMessage({
        text: `Scanned ${report.scanned}; indexed ${report.indexed} files and ${report.face_count} faces; skipped ${report.skipped}, removed ${report.removed}, failed ${report.failed}.`,
        kind: report.failed ? "error" : "success",
      });
      setConfig((current) => ({ ...current, stats: result.stats }));
      await Promise.all([loadPeople({ reset: true }), loadTitle()]);
    } catch (error) {
      setIndexMessage({ text: error.message, kind: "error" });
    } finally {
      setIndexBusy(false);
      setIndexProgress(null);
    }
  };

  const generateThumbnails = async () => {
    if (thumbBusy) return;
    setThumbBusy(true);
    setThumbMessage(null);
    setThumbProgress({ completed: 0, total: 0, ok: 0, errors: 0, startedAt: Date.now() });
    try {
      const { job_id: jobId } = await requestJson("/api/thumbnails/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      let result;
      while (!result) {
        const status = await requestJson(`/api/thumbnails/status/${encodeURIComponent(jobId)}`);
        setThumbProgress((prev) => ({
          ...prev,
          completed: status.completed,
          total: status.total,
          ok: status.ok,
          errors: status.errors,
        }));
        if (status.status === "failed") throw new Error(status.error || "Thumbnail generation failed.");
        if (status.status === "completed") result = status;
        else await new Promise((resolve) => window.setTimeout(resolve, 750));
      }
      setThumbMessage({
        text: `Done! ${result.ok} photo and face thumbnails generated, ${result.errors} errors.`,
        kind: result.errors ? "error" : "success",
      });
    } catch (error) {
      setThumbMessage({ text: error.message, kind: "error" });
    } finally {
      setThumbBusy(false);
      setThumbProgress(null);
    }
  };

  useEffect(() => {
    if (screen !== "train") return;
    let cancelled = false;

    requestJson("/api/thumbnails/status").then((status) => {
      if (cancelled || !status || status.status !== "running") return;
      setThumbBusy(true);
      setThumbProgress({
        completed: status.completed || 0,
        total: status.total || 0,
        ok: status.ok || 0,
        errors: status.errors || 0,
        startedAt: Date.now(),
      });
      const poll = async () => {
        try {
          while (!cancelled) {
            const current = await requestJson(`/api/thumbnails/status/${encodeURIComponent(status.job_id)}`);
            if (cancelled) break;
            setThumbProgress((prev) => ({
              ...prev,
              completed: current.completed,
              total: current.total,
              ok: current.ok,
              errors: current.errors,
            }));
            if (current.status === "failed") throw new Error(current.error || "Thumbnail generation failed.");
            if (current.status === "completed") {
              setThumbMessage({
                text: `Done! ${current.ok} photo and face thumbnails generated, ${current.errors} errors.`,
                kind: current.errors ? "error" : "success",
              });
              break;
            }
            await new Promise((r) => window.setTimeout(r, 750));
          }
        } catch (err) {
          if (!cancelled) setThumbMessage({ text: err.message, kind: "error" });
        } finally {
          if (!cancelled) {
            setThumbBusy(false);
            setThumbProgress(null);
          }
        }
      };
      poll();
    }).catch(() => {});
  }, [screen]);

  const currentPhoto = viewerPhotos[viewerIndex];
  const previousPhoto = viewerPhotos[(viewerIndex - 1 + viewerPhotos.length) % viewerPhotos.length];
  const nextPhoto = viewerPhotos[(viewerIndex + 1) % viewerPhotos.length];
  const hasNeighbors = viewerPhotos.length > 1;

  const stepViewer = (delta) => {
    if (!viewerPhotos.length) return;
    setViewerIndex((index) => (index + delta + viewerPhotos.length) % viewerPhotos.length);
  };

  const saveFeedback = async (value) => {
    const photo = viewerPhotos[viewerIndex];
    if (!photo || photo.source === "s3") return;
    const oldValue = feedback[photo.path] || null;
    const nextValue = oldValue === value ? null : value;
    setFeedback((current) => {
      const copy = { ...current };
      if (nextValue) copy[photo.path] = nextValue;
      else delete copy[photo.path];
      return copy;
    });
    if (value === "like" && nextValue) {
      setShowHeart(true);
      window.setTimeout(() => setShowHeart(false), 700);
    }
    try {
      await requestJson("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: photo.path, value: nextValue }),
      });
    } catch (error) {
      setFeedback((current) => {
        const copy = { ...current };
        if (oldValue) copy[photo.path] = oldValue;
        else delete copy[photo.path];
        return copy;
      });
      setViewerMessage({ text: error.message, kind: "error" });
    }
  };

  const deleteCurrentPhoto = async () => {
    const photo = viewerPhotos[viewerIndex];
    if (!photo || photo.source === "s3") return;
    if (!window.confirm(`Delete this photo from your collection?\n\n${photo.path}\n\nThis action cannot be undone.`)) return;
    try {
      const result = await requestJson("/api/delete-photo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: photo.path }),
      });
      const remaining = viewerPhotos.filter((item) => item.path !== photo.path);
      setPhotos((current) => current.filter((item) => item.path !== photo.path));
      setPhotoTotal((total) => Math.max(0, total - 1));
      if (!remaining.length) {
        setViewerPhotos([]);
        goScreen(returnScreen || "home");
        return;
      }
      setViewerPhotos(remaining);
      setViewerIndex((index) => Math.min(index, remaining.length - 1));
      setViewerMessage({ text: result.message || "Photo deleted.", kind: "success" });
    } catch (error) {
      setViewerMessage({ text: error.message, kind: "error" });
    }
  };

  const relativePhotoPath = (path) => {
    const normalizedPath = path.replace(/\\/g, "/");
    const normalizedRoot = String(config.collection || "").replace(/\\/g, "/").replace(/\/+$/, "");
    const relative = normalizedPath.toLowerCase().startsWith(`${normalizedRoot.toLowerCase()}/`)
      ? normalizedPath.slice(normalizedRoot.length + 1)
      : normalizedPath;
    const segments = relative.split("/").filter((part) => part && part !== "." && part !== "..");
    return segments.length ? segments : [normalizedPath.split("/").pop() || "photo"];
  };

  const downloadSelected = async (asZip) => {
    const chosen = photos.filter((photo) => selected.has(photo.path));
    if (!chosen.length || downloadBusy) return;
    setDownloadBusy(true);
    try {
      if (!asZip && typeof window.showDirectoryPicker === "function") {
        setGalleryMessage({ text: "Choose a destination folder in the folder picker…", kind: "info" });
        let directory;
        try {
          directory = await window.showDirectoryPicker({ mode: "readwrite" });
        } catch (error) {
          if (error.name === "AbortError") return;
          throw error;
        }
        for (let index = 0; index < chosen.length; index += 1) {
          const photo = chosen[index];
          setGalleryMessage({ text: `Saving photo ${index + 1} of ${chosen.length}…`, kind: "info" });
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
        setGalleryMessage({ text: `Saved ${chosen.length} photo${chosen.length === 1 ? "" : "s"} to the selected folder.`, kind: "success" });
        return;
      }
      setGalleryMessage({ text: "Preparing your ZIP download…", kind: "info" });
      const blob = await downloadZip(chosen.map((photo) => photo.path));
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `photos-${(galleryPerson?.name || "selected").replace(/[^a-z0-9-_]+/gi, "-")}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setGalleryMessage({ text: `Downloaded ${chosen.length} photo${chosen.length === 1 ? "" : "s"} as a ZIP file.`, kind: "success" });
    } catch (error) {
      setGalleryMessage({ text: error.message, kind: "error" });
    } finally {
      setDownloadBusy(false);
    }
  };

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape" && previewOpen) {
        setPreviewOpen(false);
        return;
      }
      if (screen !== "viewer" || !titleReadOnly) return;
      if (event.key === "ArrowLeft") stepViewer(-1);
      if (event.key === "ArrowRight") stepViewer(1);
      if (event.key === "Escape") goBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [screen, titleReadOnly, previewOpen]);

  useEffect(() => () => stopCamera(), []);

  const allLabels = useMemo(() => ({
    s3: "Images loaded from your S3 bucket. Scroll to fetch more.",
    all: photoQuery ? `Photos matching “${photoQuery}”.` : "Every photo in your collection, in one place.",
    liked: "Photos you liked.",
    disliked: "Photos you disliked.",
    deleted: "Photos in the recycle bin.",
  }), [photoQuery]);

  const emptyPhotos = photoSource === "s3"
    ? "No supported image files were found in the configured S3 bucket."
    : photoQuery
      ? "No indexed photo paths matched. Try another name, place, or moment."
    : photoFilter === "liked"
      ? "No liked photos yet. Tap the heart on a photo to save it here."
      : photoFilter === "disliked"
        ? "No disliked photos yet. Use the thumbs down on a photo to save it here."
        : photoFilter === "deleted"
          ? "No photos are in the recycle bin yet."
          : "No photos found. Index a folder from Collection to get started.";

  const toggleSelect = (path, checked) => {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(path);
      else next.delete(path);
      return next;
    });
  };
  const hasMorePhotos = photoSource === "s3" ? s3HasMore : photos.length < photoTotal;

  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="topbar">
        <button className="wordmark" type="button" onClick={() => goScreen("home")} aria-label="Find Your Pic home">
          <span className="wordmark-icon">f.</span><span>Find Your Pic</span>
        </button>
        <div className="privacy"><span></span> Your photos stay yours</div>
        {screen !== "home" ? (
          <button className="icon-button top-home" type="button" aria-label="Go back" onClick={goBack}>
            <i className="ph ph-arrow-left" aria-hidden="true"></i>
          </button>
        ) : null}
      </header>

      <main id="main" tabIndex={-1}>
        <section className={`screen home-screen${screen === "home" ? "" : " hidden"}`}>
          <div className="home-copy" data-reveal>
            <h1>Find Your Pic</h1>
            <p>Search by person, place, or moment.</p>
          </div>
          <form className="searchbar" data-reveal style={{ "--d": "100ms" }} onSubmit={(event) => { event.preventDefault(); if (textQuery.trim()) searchByText(textQuery); }}>
            <span className="search-glyph" aria-hidden="true"><i className="ph ph-magnifying-glass"></i></span>
            <input type="search" placeholder="Name, place, or moment" autoComplete="off" aria-label="Search your photos" value={textQuery} onChange={(event) => setTextQuery(event.target.value)} />
            <button type="submit" aria-label="Search"><i className="ph ph-arrow-right" aria-hidden="true"></i></button>
          </form>
          <div className="photo-actions" data-reveal style={{ "--d": "150ms" }}>
            <button className="photo-action" type="button" onClick={startCamera}>
              <span className="action-icon camera-icon" aria-hidden="true"><i className="ph ph-camera"></i></span>
              <span><strong>Camera</strong><small>Take a photo</small></span>
            </button>
            <button className="photo-action" type="button" onClick={() => uploadRef.current?.click()}>
              <span className="action-icon upload-icon" aria-hidden="true"><i className="ph ph-upload-simple"></i></span>
              <span><strong>Upload</strong><small>Choose a photo</small></span>
            </button>
            <input ref={uploadRef} type="file" accept="image/jpeg,image/png,image/webp,image/bmp" multiple hidden onChange={(event) => { const files = Array.from(event.target.files || []); event.target.value = ""; if (files.length) searchByImages(files); }} />
          </div>
          <section className="home-folders" aria-labelledby="home-folders-title" data-reveal style={{ "--d": "200ms" }}>
            <h2 id="home-folders-title">Your folders</h2>
            <div className="home-folder-grid">
              <button className="home-folder-button" type="button" onClick={() => openAll("", "liked")}><span className="home-folder-icon" aria-hidden="true"><i className="ph-fill ph-heart"></i></span><span>Liked</span></button>
              <button className="home-folder-button" type="button" onClick={() => openAll("", "disliked")}><span className="home-folder-icon" aria-hidden="true"><i className="ph ph-thumbs-down"></i></span><span>Disliked</span></button>
              <button className="home-folder-button" type="button" onClick={() => openAll("", "deleted")}><span className="home-folder-icon" aria-hidden="true"><i className="ph ph-trash"></i></span><span>Trash</span></button>
            </div>
          </section>
          <details className="search-settings" data-reveal style={{ "--d": "250ms" }}>
            <summary><i className="ph ph-sliders-horizontal" aria-hidden="true"></i> Search settings</summary>
            <div className="settings-inline">
              <label>Similarity <output>{threshold.toFixed(2)}</output>
                <input type="range" min="0" max="1" value={threshold} step="0.01" onChange={(event) => setThreshold(Number(event.target.value))} />
              </label>
              <label>Maximum matches
                <input type="number" min="1" max="1000" value={topN} onChange={(event) => setTopN(Number(event.target.value))} />
              </label>
            </div>
          </details>
          <Notice message={homeMessage?.text} kind={homeMessage?.kind} />
          <section className="tagline" aria-label="About Find Your Pic"><Tagline /></section>
          <div className="home-note"><i className="ph ph-shield-check tiny-lock" aria-hidden="true"></i> Photos loaded securely from your S3 bucket</div>
        </section>

        <section className={`screen content-screen${screen === "all" ? "" : " hidden"}`}>
          <div className="screen-heading">
            <button className="back-link" type="button" onClick={goBack}><i className="ph ph-arrow-left" aria-hidden="true"></i> {galleryPerson ? "Collection" : "Home"}</button>
            <div className="heading-title">
              <div className="eyebrow">The whole story</div>
              <h1>
                <span>{galleryPerson ? galleryPerson.name : "All photos"}</span>{" "}
                <span className="heading-count">
                  ({photoSource === "s3" ? `${photos.length}${s3HasMore ? "+" : ""}` : photoTotal.toLocaleString()})
                </span>
              </h1>
              <p>{galleryPerson
                ? `Photos grouped with ${galleryPerson.name}. Select any number to download.`
                : (allLabels[photoSource === "s3" ? "s3" : photoFilter] || allLabels.all)}</p>
            </div>
            {galleryPerson ? null : <button
              className="small-button"
              type="button"
              aria-label="Refresh photos"
              onClick={() => photoSource === "s3" ? loadS3Photos({ reset: true }) : loadPhotos({ reset: true })}
            ><i className="ph ph-arrows-clockwise" aria-hidden="true"></i></button>}
          </div>
          {photoLoading && photos.length === 0 ? (
            <div className="photo-grid skeleton-grid" aria-busy="true" aria-label="Gathering your photos">
              {Array.from({ length: 12 }).map((_, index) => (
                <div
                  key={`skeleton-photo-${index}`}
                  className="photo-tile-skeleton skeleton skeleton-shimmer"
                  style={{ animationDelay: `${(index % 6) * 100}ms` }}
                />
              ))}
            </div>
          ) : null}
          {galleryPerson ? null : photoSource !== "s3" ? (
            <div className="photo-filters">
              {["liked", "disliked", "deleted"].map((kind) => (
                <button key={kind} className={`filter-button${photoFilter === kind ? " active" : ""}`} type="button" onClick={() => { setPhotoFilter(kind); loadPhotos({ reset: true, query: photoQuery, kind, sort: photoSort }); }}>{kind === "deleted" ? "Trash" : kind[0].toUpperCase() + kind.slice(1)}</button>
              ))}
            </div>
          ) : null}
          {galleryPerson ? (
            <div className="selection-tools">
              <span>{selected.size} selected</span>
              <div>
                <button className="selection-button" type="button" onClick={() => setSelected(new Set(photos.map((photo) => photo.path)))}>Select all</button>
                <button className="selection-button" type="button" onClick={() => setSelected(new Set())}>Clear</button>
                <button className="selection-button selection-download" type="button" disabled={!selected.size || downloadBusy} onClick={() => downloadSelected(false)}>Save to folder</button>
                <button className="selection-button selection-zip" type="button" disabled={!selected.size || downloadBusy} onClick={() => downloadSelected(true)}>Download ZIP</button>
              </div>
            </div>
          ) : null}
          <Notice message={galleryMessage?.text} kind={galleryMessage?.kind} />
          <div className={`photo-grid${photos.length ? "" : " hidden"}`}>
            {photos.map((photo) => (
              galleryPerson ? (
                <div key={photo.path} className={`photo-tile selectable${selected.has(photo.path) ? " selected" : ""}`}>
                  <button className="photo-tile-open" type="button" aria-label="Open photo" onClick={() => openViewer(photos, photos.findIndex((item) => item.path === photo.path), "all", galleryPerson.name)}>
                    <LazyImage src={imageUrl(photo.path, photo.source, true)} alt="" />
                    {photo.faces ? <span className="photo-count-badge">{photo.faces} face{photo.faces === 1 ? "" : "s"}</span> : null}
                  </button>
                  <label className="photo-select">
                    <input type="checkbox" checked={selected.has(photo.path)} onChange={(event) => toggleSelect(photo.path, event.target.checked)} />
                    <span>Select</span>
                  </label>
                </div>
              ) : (
                <button key={photo.path} className="photo-tile" type="button" aria-label="Open photo" onClick={() => openViewer(photos, photos.findIndex((item) => item.path === photo.path), "all", collectionName)}>
                  <LazyImage src={imageUrl(photo.path, photo.source, true)} alt="" />
                  {photo.faces ? <span className="photo-count-badge">{photo.faces} face{photo.faces === 1 ? "" : "s"}</span> : null}
                </button>
              )
            ))}
            {photoLoading && photos.length > 0 ? (
              Array.from({ length: 4 }).map((_, index) => (
                <div
                  key={`skeleton-more-${index}`}
                  className="photo-tile-skeleton skeleton skeleton-shimmer"
                  style={{ animationDelay: `${(index % 4) * 100}ms` }}
                />
              ))
            ) : null}
          </div>
          {photos.length === 0 && !photoLoading ? (
            <div className="empty-panel">
              <i className="ph ph-images" aria-hidden="true"></i>
              <strong>No photos yet</strong>
              <span>{emptyPhotos}</span>
            </div>
          ) : null}
          {!galleryPerson && (
            <Pagination
              currentPage={photoPage || 1}
              totalItems={photoSource === "s3" ? 0 : photoTotal}
              pageSize={25}
              hasNext={photoSource === "s3" ? s3HasMore : (photoPage || 1) * 25 < photoTotal}
              hasPrev={(photoPage || 1) > 1}
              isLoading={photoLoading}
              onPageChange={(targetPage) => {
                if (photoSource === "s3") {
                  loadS3Photos({ page: targetPage });
                } else {
                  loadPhotos({ page: targetPage });
                }
              }}
            />
          )}
        </section>

        <section className={`screen content-screen${screen === "collection" ? "" : " hidden"}`}>
          <div className="screen-heading">
            <button className="back-link" type="button" onClick={() => goScreen("home")}><i className="ph ph-arrow-left" aria-hidden="true"></i> Home</button>
            <div className="heading-title">
              <div className="eyebrow">Your people</div>
              <h1>Collection</h1>
              <p>
                {peopleQuery
                  ? `${peopleTotal} collection name${peopleTotal === 1 ? "" : "s"} match “${peopleQuery}”.`
                  : people.length
                    ? `${people.length} face groups found in your photos. Tap a person to browse their photos.`
                    : "Faces found in your photos, brought together."}
              </p>
            </div>
            <button className="small-button" type="button" aria-label="Refresh people" onClick={() => loadPeople({ reset: true })}><i className="ph ph-arrows-clockwise" aria-hidden="true"></i></button>
          </div>
          <div className="collection-tools">
            <div className="collection-name-wrap">
              <label htmlFor="collection-name">Collection name</label>
              <div className="editable-name">
                <input id="collection-name" type="text" maxLength={80} value={collectionName} onChange={(event) => setCollectionName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") saveCollectionName(event.currentTarget.value); }} />
                <button className="small-button" type="button" aria-label="Save collection name" onClick={() => saveCollectionName(collectionName)}><i className="ph ph-check" aria-hidden="true"></i></button>
              </div>
            </div>
            <div className="index-tools">
              <details>
                <summary><i className="ph ph-gear-six" aria-hidden="true"></i> Index settings</summary>
                <div className="index-settings-popover">
                  <label>Index database<input type="text" value={config.database} readOnly /></label>
                  <label>Images folder<input type="text" value={config.collection} readOnly /></label>
                  <div className="settings-inline">
                    <label>Backend
                      <select value={backend} onChange={(event) => setBackend(event.target.value)}>
                        <option value="insightface">InsightFace</option>
                        <option value="dlib">dlib</option>
                      </select>
                    </label>
                    <label>Workers
                      <select value={workers} onChange={(event) => setWorkers(event.target.value)}>
                        <option>1</option><option>2</option><option>4</option>
                      </select>
                    </label>
                  </div>
                </div>
              </details>
              <button className="button button-green" type="button" disabled={indexBusy} onClick={updateIndex}><i className="ph ph-arrows-clockwise" aria-hidden="true"></i> Update index</button>
            </div>
          </div>
          {indexBusy && indexProgress ? <IndexProgress progress={indexProgress} /> : null}
          <Notice message={indexMessage?.text} kind={indexMessage?.kind} />
          {peopleLoading && people.length === 0 ? (
            <div className="people-grid skeleton-grid" aria-busy="true" aria-label="Finding familiar faces">
              {Array.from({ length: 8 }).map((_, index) => (
                <div key={`skeleton-person-${index}`} className="person-card-skeleton">
                  <div
                    className="person-avatar-skeleton skeleton skeleton-shimmer"
                    style={{ animationDelay: `${(index % 4) * 120}ms` }}
                  />
                  <div className="person-name-skeleton skeleton skeleton-shimmer" />
                  <div className="person-count-skeleton skeleton skeleton-shimmer" />
                </div>
              ))}
            </div>
          ) : null}
          <div className={`people-grid${people.length ? "" : " hidden"}`}>
            {people.map((person) => (
              <article className="person-card" key={person.key}>
                <button className="person-open" type="button" aria-label={`Browse photos of ${person.name}`} onClick={() => openPersonGallery(person)}>
                  <span className="person-avatar">
                    <LazyImage src={faceUrl(person.preview)} alt="" isAvatar />
                  </span>
                </button>
                <div className="person-label">
                  <input type="text" maxLength={80} defaultValue={person.name} aria-label="Person name" onBlur={(event) => savePersonName(person.key, event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} />
                </div>
                <div className="person-actions">
                  <button className="person-preview-button" type="button" onClick={() => openPersonGallery(person)}>Open gallery</button>
                </div>
                <span className="person-count">{person.count} photo{person.count === 1 ? "" : "s"}</span>
              </article>
            ))}
            {peopleLoading && people.length > 0 ? (
              Array.from({ length: 4 }).map((_, index) => (
                <div key={`skeleton-more-person-${index}`} className="person-card-skeleton">
                  <div
                    className="person-avatar-skeleton skeleton skeleton-shimmer"
                    style={{ animationDelay: `${(index % 4) * 120}ms` }}
                  />
                  <div className="person-name-skeleton skeleton skeleton-shimmer" />
                  <div className="person-count-skeleton skeleton skeleton-shimmer" />
                </div>
              ))
            ) : null}
          </div>
          <Pagination
            currentPage={peoplePage || 1}
            totalItems={peopleTotal}
            pageSize={25}
            hasNext={(peoplePage || 1) * 25 < peopleTotal}
            hasPrev={(peoplePage || 1) > 1}
            isLoading={peopleLoading}
            onPageChange={(targetPage) => {
              loadPeople({ page: targetPage });
            }}
          />
        </section>

        <section className={`screen viewer-screen${screen === "viewer" ? "" : " hidden"}`}>
          <div className="viewer-top">
            <button className="back-link" type="button" onClick={goBack}><i className="ph ph-arrow-left" aria-hidden="true"></i> Back</button>
            {photoSource === "s3" ? (
              <div className="viewer-title-edit"><span>S3 bucket</span></div>
            ) : (
              <div className="viewer-title-edit">
                <input
                  type="text"
                  maxLength={80}
                  value={collectionName}
                  readOnly={titleReadOnly}
                  aria-label="Editable collection title"
                  onChange={(event) => setCollectionName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      setTitleReadOnly(true);
                      saveCollectionName(event.currentTarget.value);
                    }
                    if (event.key === "Escape") {
                      setTitleReadOnly(true);
                      loadTitle();
                    }
                  }}
                />
                <button className="icon-button" type="button" aria-label="Edit collection title" onClick={() => {
                  if (!titleReadOnly) saveCollectionName(collectionName);
                  setTitleReadOnly((value) => !value);
                }}><i className={`ph ${titleReadOnly ? "ph-pencil-simple" : "ph-check"}`} aria-hidden="true"></i></button>
              </div>
            )}
            <span className="viewer-position">{viewerPhotos.length ? `${viewerIndex + 1} / ${viewerPhotos.length}` : ""}</span>
          </div>
          <div
            className="viewer-stage"
            aria-label="Photo viewer"
            onTouchStart={(event) => { touchStartX.current = event.changedTouches[0].clientX; }}
            onTouchEnd={(event) => {
              if (touchStartX.current === undefined) return;
              const delta = event.changedTouches[0].clientX - touchStartX.current;
              if (Math.abs(delta) > 55) stepViewer(delta < 0 ? 1 : -1);
              touchStartX.current = undefined;
            }}
          >
            <button className="stack-card stack-left" type="button" aria-label="Previous photo" disabled={!hasNeighbors} onClick={() => stepViewer(-1)}>
              {previousPhoto ? <LazyImage src={imageUrl(previousPhoto.path, previousPhoto.source, true)} alt="" /> : null}
            </button>
            <button className="viewer-arrow viewer-arrow-left" type="button" aria-label="Previous photo" disabled={!hasNeighbors} onClick={() => stepViewer(-1)}><i className="ph ph-caret-left" aria-hidden="true"></i></button>
            <div
              className="main-photo-wrap"
              onDoubleClick={() => { if (currentPhoto?.source !== "s3") saveFeedback("like"); }}
              onTouchEnd={(event) => {
                if (currentPhoto?.source === "s3") return;
                const now = Date.now();
                if (now - lastTap.current < 320) {
                  event.preventDefault();
                  saveFeedback("like");
                }
                lastTap.current = now;
              }}
            >
              {currentPhoto ? (
                <LazyImage
                  key={currentPhoto.path}
                  id="main-photo"
                  alt={fileBase(currentPhoto.path)}
                  draggable="false"
                  src={imageUrl(currentPhoto.path, currentPhoto.source)}
                  eager
                  stage
                />
              ) : null}
              <span className={`like-heart${showHeart ? "" : " hidden"}`} aria-hidden="true"><i className="ph-fill ph-heart"></i></span>
            </div>
            <button className="viewer-arrow viewer-arrow-right" type="button" aria-label="Next photo" disabled={!hasNeighbors} onClick={() => stepViewer(1)}><i className="ph ph-caret-right" aria-hidden="true"></i></button>
            <button className="stack-card stack-right" type="button" aria-label="Next photo" disabled={!hasNeighbors} onClick={() => stepViewer(1)}>
              {nextPhoto ? <LazyImage src={imageUrl(nextPhoto.path, nextPhoto.source, true)} alt="" /> : null}
            </button>
          </div>
          <div className="viewer-caption">
            <div>
              <div className="photo-filename" title={currentPhoto?.path}>{currentPhoto ? fileBase(currentPhoto.path) : ""}</div>
              <div className="photo-subtitle">{currentPhoto?.faces ? `${currentPhoto.faces} detected face${currentPhoto.faces === 1 ? "" : "s"}` : ""}</div>
            </div>
            <div className="viewer-actions">
              <button className="preview-button" type="button" onClick={() => setPreviewOpen(true)}><i className="ph ph-eye" aria-hidden="true"></i> Preview</button>
              <button className="download-button" type="button" onClick={() => {
                if (!currentPhoto) return;
                const link = document.createElement("a");
                link.href = imageUrl(currentPhoto.path, currentPhoto.source);
                link.download = fileBase(currentPhoto.path) || "photo";
                document.body.appendChild(link);
                link.click();
                link.remove();
              }}><i className="ph ph-download-simple" aria-hidden="true"></i> Download</button>
              {currentPhoto?.source === "s3" ? null : (
                <>
                  <button className="delete-button" type="button" onClick={deleteCurrentPhoto}><i className="ph ph-trash" aria-hidden="true"></i> Delete</button>
                  <span className="doubletap-hint">Double tap to like</span>
                </>
              )}
            </div>
          </div>
          <Notice message={viewerMessage?.text} kind={viewerMessage?.kind} />
          {currentPhoto?.source === "s3" ? null : (
            <div className="feedback-row" aria-label="Photo feedback">
              <button className={`feedback-button${feedback[currentPhoto?.path] === "down" ? " active" : ""}`} type="button" aria-label="Thumbs down" onClick={() => saveFeedback("down")}><i className="ph ph-thumbs-down" aria-hidden="true"></i></button>
              <button className="feedback-button like-button" type="button" aria-label="Like photo" onClick={() => saveFeedback("like")}><i className="ph ph-heart" aria-hidden="true"></i></button>
              <button className={`feedback-button${feedback[currentPhoto?.path] === "up" ? " active" : ""}`} type="button" aria-label="Thumbs up" onClick={() => saveFeedback("up")}><i className="ph ph-thumbs-up" aria-hidden="true"></i></button>
            </div>
          )}
          <div className="viewer-hint">Swipe or use the arrows to browse</div>
        </section>
        {/* Train screen */}
        <section className={`screen content-screen${screen === "train" ? "" : " hidden"}`}>
          <div className="screen-heading">
            <button className="back-link" type="button" onClick={() => goScreen("home")}><i className="ph ph-arrow-left" aria-hidden="true" /> Home</button>
            <div className="heading-title">
              <div className="eyebrow">Background jobs</div>
              <h1>Train</h1>
              <p>Run face indexing and thumbnail generation from the browser.</p>
            </div>
          </div>
          <div className="train-card">
            <div className="train-card-header">
              <i className="ph ph-brain" aria-hidden="true" style={{ fontSize: "1.5rem", color: "var(--accent)" }} />
              <div>
                <strong>Index Faces</strong>
                <p>Scan all S3 photos, detect faces and store embeddings in the database.</p>
              </div>
            </div>
            <div className="index-tools" style={{ marginTop: "0.75rem" }}>
              <details>
                <summary><i className="ph ph-gear-six" aria-hidden="true" /> Settings</summary>
                <div className="index-settings-popover">
                  <div className="settings-inline">
                    <label>Backend
                      <select value={backend} onChange={(e) => setBackend(e.target.value)}>
                        <option value="insightface">InsightFace</option>
                        <option value="dlib">dlib</option>
                      </select>
                    </label>
                    <label>Workers
                      <select value={workers} onChange={(e) => setWorkers(e.target.value)}>
                        <option>1</option><option>2</option><option>4</option>
                      </select>
                    </label>
                  </div>
                </div>
              </details>
              <button className="button button-green" type="button" disabled={indexBusy || thumbBusy} onClick={updateIndex}>
                {indexBusy ? <><span className="spinner" /> Indexing...</> : <><i className="ph ph-arrows-clockwise" aria-hidden="true" /> Start indexing</>}
              </button>
            </div>
            {indexBusy && indexProgress ? <IndexProgress progress={indexProgress} /> : null}
            <Notice message={indexMessage?.text} kind={indexMessage?.kind} />
          </div>
          <div className="train-card">
            <div className="train-card-header">
              <i className="ph ph-images" aria-hidden="true" style={{ fontSize: "1.5rem", color: "var(--accent)" }} />
              <div>
                <strong>Generate Photo &amp; Face Thumbnails</strong>
                <p>Pre-generate WebP thumbnails for S3 photos and indexed face groups. Already-generated ones are reused.</p>
              </div>
            </div>
            <div style={{ marginTop: "0.75rem" }}>
              <button className="button button-green" type="button" disabled={thumbBusy || indexBusy} onClick={generateThumbnails}>
                {thumbBusy ? <><span className="spinner" /> Generating...</> : <><i className="ph ph-image" aria-hidden="true" /> Generate photo &amp; face thumbnails</>}
              </button>
            </div>
            {thumbBusy && thumbProgress && thumbProgress.total > 0 ? (
              <div className="index-progress" role="status" aria-live="polite" aria-busy="true">
                <div className="index-progress-heading">
                  <span>Thumbnail items: {thumbProgress.completed} of {thumbProgress.total} ({Math.floor((thumbProgress.completed / thumbProgress.total) * 100)}%)</span>
                  <span>{thumbProgress.completed >= 2 && thumbProgress.completed < thumbProgress.total
                    ? formatTimeRemaining(((Date.now() - thumbProgress.startedAt) / 1000 / thumbProgress.completed) * (thumbProgress.total - thumbProgress.completed))
                    : "Estimating..."}</span>
                </div>
                <progress className="index-progress-bar" max={thumbProgress.total} value={thumbProgress.completed} aria-label="Thumbnail generation progress" />
              </div>
            ) : null}
            <Notice message={thumbMessage?.text} kind={thumbMessage?.kind} />
          </div>
        </section>
      </main>

      <nav className={`bottom-nav${screen === "viewer" ? " hidden" : ""}`} aria-label="Main navigation">
        <button className={`nav-button${screen === "collection" ? " active" : ""}`} type="button" onClick={openCollection}><i className="ph ph-users-three nav-icon" aria-hidden="true"></i><span>Collection</span></button>
        <button className={`nav-button${screen === "all" && !galleryPerson ? " active" : ""}`} type="button" onClick={() => openAll()}><i className="ph ph-squares-four nav-icon" aria-hidden="true"></i><span>All</span></button>
        <button className={`nav-button${screen === "train" ? " active" : ""}`} type="button" onClick={() => goScreen("train")}><i className="ph ph-lightning nav-icon" aria-hidden="true"></i><span>Train</span></button>
      </nav>

      <div className={`camera-modal${cameraOpen ? "" : " hidden"}`} role="dialog" aria-modal="true" aria-label="Take a photo">
        <div className="camera-card">
          <button className="camera-close" type="button" aria-label="Close camera" onClick={() => { setCameraOpen(false); stopCamera(); }}><i className="ph ph-x" aria-hidden="true"></i></button>
          <h2>Take a photo</h2>
          <p>Find similar moments in your collection.</p>
          <video ref={videoRef} autoPlay playsInline muted></video>
          {cameraError ? <div className="camera-fallback">{cameraError}</div> : null}
          <button className="button button-green button-wide" type="button" disabled={Boolean(cameraError)} onClick={capturePhoto}><i className="ph ph-camera" aria-hidden="true"></i> Take photo</button>
        </div>
      </div>
      <div className={`camera-modal${faceChoices ? "" : " hidden"}`} role="dialog" aria-modal="true" style={{ zIndex: 60 }}>
        <div className="camera-card face-picker-card">
          <button className="camera-close" type="button" aria-label="Cancel face selection" onClick={() => { faceResolve.current?.(null); setFaceChoices(null); }}><i className="ph ph-x" aria-hidden="true"></i></button>
          <h2>Which face should I look for?</h2>
          <p>
            {faceChoices?.faces?.length || 0} faces found in this reference photo.
          </p>
          <div style={{ padding: "0 12px 12px 12px", display: "flex", flexDirection: "column", gap: "8px" }}>
            <button
              className="button button-green button-wide"
              type="button"
              onClick={() => {
                const faces = Array.isArray(faceChoices) ? faceChoices : (faceChoices?.faces || []);
                const allIndices = faces.map((f) => f.index);
                faceResolve.current?.(allIndices);
                setFaceChoices(null);
              }}
            >
              <i className="ph ph-users" aria-hidden="true"></i> Search for all faces
            </button>
          </div>
          <div className="face-choice-list">
            {(Array.isArray(faceChoices) ? faceChoices : (faceChoices?.faces || [])).map((face, index) => (
              <button
                key={face.index}
                type="button"
                className="button button-outline"
                style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "12px" }}
                onClick={() => { faceResolve.current?.(face.index); setFaceChoices(null); }}
              >
                <i className="ph ph-user" style={{ fontSize: "24px", marginBottom: "4px" }} aria-hidden="true"></i>
                <strong>Face {index + 1}</strong>
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className={`photo-preview-modal${previewOpen ? "" : " hidden"}`} role="dialog" aria-modal="true" onClick={(event) => { if (event.target === event.currentTarget) setPreviewOpen(false); }}>
        <button className="photo-preview-close" type="button" aria-label="Close photo preview" onClick={() => setPreviewOpen(false)}><i className="ph ph-x" aria-hidden="true"></i></button>
        {currentPhoto ? <LazyImage alt={fileBase(currentPhoto.path)} src={imageUrl(currentPhoto.path, currentPhoto.source)} eager stage /> : null}
      </div>
      <div className={`search-overlay${searchOverlay ? "" : " hidden"}`} role="status" aria-live="polite">
        <div className="search-overlay-card">
          <span className="spinner spinner-large"></span>
          <strong>{searchOverlay?.title}</strong>
          <span>{searchOverlay?.detail}</span>
        </div>
      </div>
    </>
  );
}
