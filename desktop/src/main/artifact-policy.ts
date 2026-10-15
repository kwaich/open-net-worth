import { trustedFrame } from './security';

interface ArtifactFrameContext {
  ownedContentsId: number;
  requestContentsId: number | undefined;
  resourceType: string;
  mainFrame: { url: string; routingId: number };
  frame: { parent: object | null } | null | undefined;
}

export function artifactFrameAllowed(origin: string, context: ArtifactFrameContext): boolean {
  return context.ownedContentsId === context.requestContentsId && context.resourceType === 'subFrame'
    && context.frame?.parent === context.mainFrame
    && trustedFrame(origin, context.mainFrame.url, context.mainFrame.routingId, context.mainFrame.routingId);
}

export function artifactRequest(value: string): { id: string; type: 'html' | 'svg' } | null {
  try {
    const url = new URL(value);
    const id = url.pathname.slice(1);
    const type = url.searchParams.get('type');
    if (url.protocol !== 'onw-artifact:' || url.hostname !== 'preview' || url.username || url.password || url.port || url.hash || !/^art_[a-zA-Z0-9_-]{1,100}$/.test(id) || !['html', 'svg'].includes(type ?? '') || [...url.searchParams.keys()].some(key => key !== 'type') || url.searchParams.getAll('type').length !== 1) return null;
    return { id, type: type as 'html' | 'svg' };
  } catch { return null; }
}

export function artifactPolicy(type: 'html' | 'svg', appOrigin: string): string {
  return `sandbox${type === 'html' ? ' allow-scripts' : ''}; default-src 'none'; script-src ${type === 'html' ? "'unsafe-inline'" : "'none'"}; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors ${appOrigin};`;
}
