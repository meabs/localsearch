function confidenceBadge(status) {
  const cssClass =
    status === "SUPPORTED"
      ? "badge badge-supported"
      : status === "PARTIALLY_SUPPORTED"
        ? "badge badge-partial"
        : "badge badge-unsupported";
  return `<span class="${cssClass}">${status}</span>`;
}
