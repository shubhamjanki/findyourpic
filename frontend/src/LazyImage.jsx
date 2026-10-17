import { useEffect, useRef, useState } from "react";

export default function LazyImage({
  src,
  alt = "",
  className = "",
  id,
  draggable,
  eager = false,
  isAvatar = false,
  stage = false,
  onLoad,
  onError,
  style,
  ...props
}) {
  const [isInView, setIsInView] = useState(eager);
  const [isLoaded, setIsLoaded] = useState(false);
  const [hasError, setHasError] = useState(false);
  const containerRef = useRef(null);

  // Setup intersection observer for lazy loading off-screen images
  useEffect(() => {
    if (eager) {
      setIsInView(true);
      return undefined;
    }

    const node = containerRef.current;
    if (!node) return undefined;

    if (!("IntersectionObserver" in window)) {
      setIsInView(true);
      return undefined;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setIsInView(true);
            observer.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "300px 0px 300px 0px", threshold: 0 }
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, [eager, src]);

  // When src changes, reset loaded and error states
  useEffect(() => {
    setIsLoaded(false);
    setHasError(false);
  }, [src]);

  const handleLoad = (e) => {
    setIsLoaded(true);
    if (onLoad) onLoad(e);
  };

  const handleError = (e) => {
    setHasError(true);
    setIsLoaded(false);
    if (onError) onError(e);
  };

  const wrapperClasses = [
    "lazy-img-wrapper",
    isAvatar ? "avatar-wrapper" : "",
    stage ? "stage-img-wrapper" : "",
    isLoaded ? "is-loaded" : "",
    hasError ? "has-error" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div ref={containerRef} className={wrapperClasses} style={style}>
      {!isLoaded && !hasError && (
        <div
          className={`skeleton skeleton-shimmer ${isAvatar ? "rounded-full" : ""}`}
          aria-hidden="true"
        />
      )}
      {hasError && (
        <div className="skeleton-error" aria-label="Photo unavailable">
          <i className="ph ph-image-broken" aria-hidden="true"></i>
          {!isAvatar && <span className="skeleton-error-text">Unavailable</span>}
        </div>
      )}
      {isInView && !hasError && (
        <img
          ref={(imgNode) => {
            if (imgNode && imgNode.complete && imgNode.naturalWidth > 0 && !isLoaded) {
              setIsLoaded(true);
            }
          }}
          id={id}
          src={src}
          alt={alt}
          loading={eager ? "eager" : "lazy"}
          decoding="async"
          draggable={draggable}
          className={`lazy-img ${isLoaded ? "is-loaded" : "is-loading"}`}
          onLoad={handleLoad}
          onError={handleError}
          {...props}
        />
      )}
    </div>
  );
}
