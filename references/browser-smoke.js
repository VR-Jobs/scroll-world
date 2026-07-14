/* Run in the finished page's browser context after assets have loaded. */
(async function scrollWorldBrowserSmoke() {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const videos = [...document.querySelectorAll("video")];
  const initialTimes = videos.map((video) => video.currentTime);
  const initialScroll = window.scrollY;
  const target = Math.max(0, (document.documentElement.scrollHeight - innerHeight) * 0.45);

  window.scrollTo({ top: target, behavior: "auto" });
  await wait(700);

  const rows = videos.map((video, index) => ({
    index,
    poster: Boolean(video.getAttribute("poster")),
    muted: video.muted,
    playsinline: video.hasAttribute("playsinline"),
    readyState: video.readyState,
    seekableEnd: video.seekable.length ? video.seekable.end(video.seekable.length - 1) : 0,
    currentTimeChanged: Math.abs(video.currentTime - initialTimes[index]) > 0.01,
    source: video.currentSrc || video.querySelector("source")?.src || null,
  }));
  const result = {
    url: location.href,
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
    h1Count: document.querySelectorAll("h1").length,
    horizontalOverflowPx: Math.max(0, document.documentElement.scrollWidth - innerWidth),
    videoCount: videos.length,
    videos: rows,
    pass: document.querySelectorAll("h1").length === 1
      && document.documentElement.scrollWidth <= innerWidth + 1
      && rows.length > 0
      && rows.every((row) => row.poster && row.muted && row.playsinline)
      && rows.some((row) => row.seekableEnd > 0)
      && rows.some((row) => row.currentTimeChanged),
  };
  window.scrollTo({ top: initialScroll, behavior: "auto" });
  console.table(rows);
  console.log("scroll-world browser smoke", result);
  return result;
})();
