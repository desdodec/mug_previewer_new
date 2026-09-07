/* Shared byte-exact hashing, runnable in the browser and Node test fixtures. */
async function reviewedSvgSha256(bytes) {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}
function svgBytesDataUrl(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return 'data:image/svg+xml;base64,' + btoa(binary);
}
if (typeof module !== 'undefined') module.exports = {reviewedSvgSha256, svgBytesDataUrl};
