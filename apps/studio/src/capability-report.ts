import type { CapabilitiesWire, Capability, Role, RoleOption } from './protocol';

export interface ReportPicture {
  dataUrl: string;
  capturedAt: string;
  description: 'Current rendered microscope canvas; not a historical request recording';
}

/** An explicit allowlist: no full settings, credentials, grants or conversations. */
function reportContext(wire: CapabilitiesWire, outcome: string, note: string, picture: ReportPicture | null) {
  return {
    build: wire.build,
    setup: { edition: wire.setup?.edition ?? 'Unknown', models: Object.fromEntries(wire.roles.map(role => [role.id, role.current ?? null])) },
    outcome, note: note.trim().slice(0, 2000),
    request: null,
    missingEvidence: ['No request was selected. Historical steps, requesting-browser ownership and recording channels are not inferred.'],
    picture,
    destination: 'Local file only; nothing is uploaded or published',
  };
}

export function capabilityReport(wire: CapabilitiesWire, capability: Capability, outcome: string, note: string, picture: ReportPicture | null) {
  return { ...reportContext(wire, outcome, note, picture),
    schema: 'micromind-local-capability-report-v1',
    capability: {id:capability.id, title:capability.title, available:capability.available},
    verdictNote: `${outcome}: ${note.trim()}`.slice(0, 300),
  };
}

/** A model option is not a capability verdict. Keep its declared identity without guessing an affected task. */
export function modelReport(wire: CapabilitiesWire, role: Role, option: RoleOption, outcome: string, note: string, picture: ReportPicture | null) {
  return { ...reportContext(wire, outcome, note, picture),
    schema: 'micromind-local-model-report-v1',
    model: {role:role.id, id:option.id, label:option.label, available:option.available,
      selected:role.current === option.id, licence:option.licence ?? null, tier:option.tier ?? null,
      sources:(option.sources ?? []).filter(source => /^https?:\/\//.test(source.url))
        .map(source => ({title:source.title, url:source.url, licence:source.licence}))},
  };
}

/** Called only after explicit picture opt-in. Reads an existing canvas, never starts capture. */
export function reportPicture(source: HTMLCanvasElement | null): ReportPicture {
  if (!source?.width || !source.height) throw new Error('No rendered microscope view is available.');
  const canvas = document.createElement('canvas');
  const scale = Math.min(1, 1280 / Math.max(source.width, source.height));
  canvas.width = Math.max(1, Math.round(source.width * scale));
  canvas.height = Math.max(1, Math.round(source.height * scale));
  const context = canvas.getContext('2d');
  if (!context) throw new Error('This browser cannot copy the rendered view.');
  context.drawImage(source, 0, 0, canvas.width, canvas.height);
  const dataUrl = canvas.toDataURL('image/jpeg', .8);
  if (!dataUrl.startsWith('data:image/jpeg;base64,') || dataUrl.length > 2_000_000) throw new Error('The rendered view could not be copied within the local report limit.');
  return {dataUrl, capturedAt:new Date().toISOString(), description:'Current rendered microscope canvas; not a historical request recording'};
}

export function downloadCapabilityReport(report: ReturnType<typeof capabilityReport> | ReturnType<typeof modelReport>): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type:'application/json'}));
  const link = document.createElement('a');
  const id = 'capability' in report ? report.capability.id : `${report.model.role}-${report.model.id}`;
  link.href = url; link.download = `micromind-problem-${id.replace(/[^a-zA-Z0-9_.-]/g, '-')}.json`;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
