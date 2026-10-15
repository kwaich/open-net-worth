import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const assets = JSON.parse(readFileSync(join(__dirname, '..', '..', 'resources', 'onboarding.generated.json'), 'utf8')) as { css: string; logo: string };

export const escapeHtml = (value: string): string => value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!);

// Turns a message from an IPC rejection into the sentence a person should read.
export const FAILURE_TEXT = "error.message.replace(/^Error invoking remote method '[^']+': Error: /,'')";

// The shared shell of the app's own screens (waiting, progress, state): same type, colours and buttons as
// Welcome and the connection dialog. `script` runs under a nonce, so these pages carry no other script.
export function nativeDocument(options: { theme: 'dark' | 'light'; title: string; body: string; script: string; nonce: string }): string {
  const { theme, title, body, script, nonce } = options;
  return `<!doctype html><html lang="en" data-theme="${theme}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; font-src data:; script-src 'nonce-${nonce}';"><title>${escapeHtml(title)}</title><style>${assets.css}
*{box-sizing:border-box}html{color-scheme:${theme}}body{background:var(--bg);color:var(--ink);font:400 var(--text-md)/1.72 'Plus Jakarta Sans',-apple-system,system-ui,sans-serif;-webkit-font-smoothing:antialiased;margin:0;min-height:100vh;display:grid;place-items:center}main{padding:36px 40px;width:100%;max-width:760px}svg{display:block;color:var(--muted)}h1{font-size:var(--text-display-xl);font-weight:400;line-height:1.16;letter-spacing:-.028em;margin:24px 0 16px}p{color:var(--muted);margin:0 0 12px}label{display:block;color:var(--muted);font-size:var(--text-base);margin-bottom:8px}input{display:block;width:100%;padding:12px;border:1px solid var(--line);border-radius:6px;background:var(--surface2);color:var(--ink);font:inherit;letter-spacing:.08em;text-transform:uppercase}input:focus-visible,button:focus-visible{outline:2px solid var(--fr-gold);outline-offset:3px}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:24px}button{border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--ink);padding:10px 18px;font:600 var(--text-base)/1.5 'Plus Jakarta Sans',sans-serif;cursor:pointer}button.primary{background:var(--fr-gold);border-color:var(--fr-gold);color:var(--bg)}button.link{border-color:transparent;color:var(--fr-gold)}button:disabled{opacity:.5;cursor:wait}[hidden]{display:none}#error{margin-top:16px;color:var(--ink)}.bar{height:8px;border-radius:4px;background:var(--surface2);border:1px solid var(--line);overflow:hidden;margin:20px 0 12px}.bar>span{display:block;height:100%;width:0;background:var(--fr-gold);transition:width .3s}.bar.busy>span{width:35%;animation:slide 1.2s ease-in-out infinite alternate}@keyframes slide{from{margin-left:0}to{margin-left:65%}}@media(prefers-reduced-motion:reduce){.bar.busy>span{animation:none}}@media(max-width:640px){main{padding:28px}.actions{flex-direction:column}.actions button{width:100%}}
.titlebar{display:none}html[data-desktop-chrome=mac] .titlebar{display:block;position:fixed;inset:0 0 auto;height:48px;-webkit-app-region:drag}html[data-desktop-chrome=mac] body{padding-top:48px}
</style></head><body><div class="titlebar" aria-hidden="true"></div><main>${assets.logo}${body}<p id="error" role="alert"></p></main><script nonce="${nonce}">
const fail=error=>{document.getElementById('error').textContent=${FAILURE_TEXT}};const run=async action=>{const buttons=[...document.querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);document.getElementById('error').textContent='';try{await action()}catch(error){fail(error)}finally{buttons.forEach(button=>button.disabled=false)}};
${script}
</script></body></html>`;
}
