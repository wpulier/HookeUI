// Bounded observations through the ordinary Python console; no alternate worker or private data endpoint.
import {createHash} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
const MAX_BYTES = 256 << 10, CHUNK = 9000; // base64 stays below the console's 16 KiB output limit

export async function readConsoleJSON(python, expression, {raw = false} = {}) {
  const first = JSON.parse(await python(`import json as _json, base64 as _p2_base64, hashlib as _p2_hashlib
_p2_observation = _json.dumps(${expression}, allow_nan=False).encode('utf-8')
print(_json.dumps({'bytes': len(_p2_observation), 'sha256': _p2_hashlib.sha256(_p2_observation).hexdigest(), 'chunk': _p2_base64.b64encode(_p2_observation[:${CHUNK}]).decode('ascii')}))`));
  if (!Number.isSafeInteger(first.bytes) || first.bytes < 1 || first.bytes > MAX_BYTES) {
    throw new Error('JSON exceeds the bounded observation allowance');
  }
  const chunks = [Buffer.from(first.chunk, 'base64')];
  for (let offset = CHUNK; offset < first.bytes; offset += CHUNK) {
    chunks.push(Buffer.from(JSON.parse(await python(
      `print(_json.dumps(_p2_base64.b64encode(_p2_observation[${offset}:${offset + CHUNK}]).decode('ascii')))`)), 'base64'));
  }
  const body = Buffer.concat(chunks);
  if (body.length !== first.bytes || createHash('sha256').update(body).digest('hex') !== first.sha256) {
    throw new Error('Console observation length or digest changed');
  }
  const text = body.toString('utf8');
  // Keep the initial layer-data tuple opaque: JS must not round integers or erase Python float spellings.
  return raw ? text : JSON.parse(text);
}

export async function stopScientificSession(document, until) {
  // The native Session details starts collapsed. Locate the exact hidden control
  // only to find its owning section, then open the summary with an ordinary click.
  const stop = document.button('Stop scientific session', {includeHidden:true});
  const poll = {timeout:500};
  if (!(await stop.isEnabled(poll))) {
    if (!(await document.button('Open session').isEnabled(poll))) throw new Error('Session cleanup state is unavailable');
    return {ok:true, stopped:false};
  }
  const section = stop.locator('xpath=ancestor::details[1]');
  if (await section.getAttribute('open', poll) === null) {
    await section.locator(':scope > summary').filter({hasText:/^Session$/}).click({timeout:5000});
  }
  await stop.click({timeout:5000});
  await until(async () => !(await stop.isEnabled(poll)) && await document.button('Open session').isEnabled(poll),
    60, 'the owned scientific session to stop');
  return {ok:true, stopped:true};
}


export function appearanceTransaction(body, frame, expectedName, captured) {
  const change = body?.request?.request;
  const kind = {'image-presentation':'image', 'surface-presentation':'surface'}[change?.operation];
  if (body?.request?.op !== 'change' || !kind || !change.value?.settings)
    throw new Error('Expected a submitted appearance transaction');
  const targets = frame?.layers?.filter(layer => layer.id === change.value.layer) ?? [];
  if (targets.length !== 1 || targets[0].name !== expectedName || targets[0].type !== kind)
    throw new Error('Appearance transaction target does not match the owned layer');
  if (!captured || !isDeepStrictEqual(change.value.settings, captured))
    throw new Error('Submitted appearance settings differ from the complete pre-Apply UI');
  // The oracle comes from the independent form capture, after verifying that the
  // product submitted exactly those values. Keep unchanged fields and JS scalar types.
  return [change.operation, expectedName, structuredClone(captured)];
}


export async function captureAppearanceSettings(label, kind, name) {
  const number = async title => {
    const text = await label(`${title} ${name}`).inputValue();
    const value = Number(text);
    if (!text.trim() || !Number.isFinite(value)) throw new Error(`Invalid pre-Apply ${title}`);
    return value;
  };
  const pair = async prefix => [await number(`${prefix} minimum`), await number(`${prefix} maximum`)];
  const checked = title => label(`${title} ${name}`).isChecked();
  if (kind === 'image') return {
    contrastLimits: await pair('Contrast'), gamma: await number('Gamma'),
    rendering: await label(`Volume rendering ${name}`).inputValue(),
    isoThreshold: await number('Isosurface threshold'), attenuation: await number('Attenuation'),
    autoContrast: await checked('Automatic contrast'),
  };
  if (kind !== 'surface') throw new Error('Unknown pre-Apply appearance family');
  const line = async (prefix, normals) => ({
    visible: await checked(`Show ${prefix.toLowerCase()}`), width: await number(`${prefix} width`),
    color: await Promise.all(['red','green','blue','alpha'].map(channel => number(`${prefix} ${channel}`))),
    ...(normals ? {length:await number(`${prefix} length`)} : {}),
  });
  return {
    shading: await label(`Shading ${name}`).inputValue(), gamma: await number('Surface gamma'),
    contrastLimits: await pair('Surface contrast'), autoContrast: await checked('Automatic surface contrast'),
    wireframe: await line('Wireframe', false), faceNormals: await line('Face normals', true),
    vertexNormals: await line('Vertex normals', true),
  };
}

export function isOwnedViewChange(request, review) {
  return typeof review === 'string' && /^[A-Za-z0-9_-]{16,128}$/.test(review)
    && request.method() === 'POST'
    && new URL(request.url()).pathname === `/api/scientific-reviews/${review}/request`
    && request.postDataJSON()?.request?.op === 'change';
}
