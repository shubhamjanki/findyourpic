export default function Pagination({
  currentPage = 1,
  totalItems = 0,
  pageSize = 25,
  onPageChange,
  hasNext,
  hasPrev,
  isLoading = false,
}) {
  const totalPages = totalItems > 0 ? Math.ceil(totalItems / pageSize) : (hasNext ? currentPage + 1 : currentPage);

  // If there's only 1 page and no other pages, hide pagination
  if (totalPages <= 1 && !hasNext && !hasPrev) return null;

  const startItem = (currentPage - 1) * pageSize + 1;
  const endItem = totalItems > 0 ? Math.min(currentPage * pageSize, totalItems) : currentPage * pageSize;

  // Calculate visible page numbers with ellipsis
  const getPageNumbers = () => {
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    if (currentPage <= 4) {
      return [1, 2, 3, 4, 5, "...", totalPages];
    }
    if (currentPage >= totalPages - 3) {
      return [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
    }
    return [1, "...", currentPage - 1, currentPage, currentPage + 1, "...", totalPages];
  };

  const pages = totalItems > 0 ? getPageNumbers() : [];

  return (
    <div className="pagination-bar" role="navigation" aria-label="Pagination Navigation">
      <div className="pagination-info">
        {totalItems > 0 ? (
          <span>
            Showing <strong>{startItem.toLocaleString()}–{endItem.toLocaleString()}</strong> of <strong>{totalItems.toLocaleString()}</strong>
          </span>
        ) : (
          <span>
            Page <strong>{currentPage}</strong>
          </span>
        )}
      </div>

      <div className="pagination-controls">
        <button
          className="pagination-btn pagination-prev"
          type="button"
          disabled={currentPage <= 1 || (hasPrev === false) || isLoading}
          onClick={() => onPageChange(currentPage - 1)}
          aria-label="Previous page"
        >
          <i className="ph ph-caret-left" aria-hidden="true"></i>
          <span>Previous</span>
        </button>

        <div className="pagination-pages">
          {pages.map((p, idx) => (
            p === "..." ? (
              <span key={`dots-${idx}`} className="pagination-ellipsis">…</span>
            ) : (
              <button
                key={`page-${p}`}
                type="button"
                className={`pagination-number${currentPage === p ? " active" : ""}`}
                disabled={isLoading}
                onClick={() => onPageChange(p)}
                aria-current={currentPage === p ? "page" : undefined}
                aria-label={`Go to page ${p}`}
              >
                {p}
              </button>
            )
          ))}
        </div>

        <button
          className="pagination-btn pagination-next"
          type="button"
          disabled={(totalItems > 0 ? currentPage >= totalPages : !hasNext) || isLoading}
          onClick={() => onPageChange(currentPage + 1)}
          aria-label="Next page"
        >
          <span>Next</span>
          <i className="ph ph-caret-right" aria-hidden="true"></i>
        </button>
      </div>
    </div>
  );
}
