/** Owner controls only. Outside agents cannot change Project membership through the door. */
export async function mountProjectAccess(host: HTMLElement, connection: { id: string; label: string }, request: typeof fetch): Promise<void> {
  const panel = document.createElement('fieldset');
  const legend = document.createElement('legend'); legend.textContent = `Project access for ${connection.label}`;
  const status = document.createElement('p'); status.setAttribute('role', 'status'); status.textContent = 'Reading Project access…';
  panel.append(legend, status); host.append(panel);
  try {
    const reply = await request('/api/agent-project-access', { cache: 'no-store' });
    const data = await reply.json();
    if (!reply.ok || !Array.isArray(data.projects)) throw new Error(data.says ?? 'Project access could not be read.');
    if (!data.editable) { status.textContent = 'Project access changes need the qualified owner confirmation in Hooke. This copy cannot grant access yet.'; return; }
    type Row = { session: string; name: string; by: string; grants?: Record<string, string> };
    const projects: Row[] = data.projects.filter((p: unknown): p is Row => !!p && typeof p === 'object' &&
      'session' in p && typeof p.session === 'string' && 'name' in p && typeof p.name === 'string' && 'by' in p && typeof p.by === 'string');
    if (!projects.length) { status.textContent = 'No agent Project exists yet. Create a Project through the connector first.'; return; }
    const choose = document.createElement('select'); choose.setAttribute('aria-label', 'Project to share');
    const option = (label: string, value: string) => { const o = document.createElement('option'); o.textContent = label; o.value = value; return o; };
    for (const p of projects) choose.append(option(`${p.name || 'Untitled project'} (${p.session})`, p.session));
    choose.value = projects[0].session;
    const role = document.createElement('select'); role.setAttribute('aria-label', 'Project permission');
    role.append(option('No access', ''), option('Read', 'read'), option('Contribute', 'contribute'));
    const save = document.createElement('button'); save.type = 'button'; save.textContent = 'Save Project access';
    const explain = document.createElement('p');
    explain.textContent = 'Read allows inspecting retained sources and results. Contribute also allows importing material and running research. Neither allows changing membership, credentials or spending.';
    const selected = () => projects.find(p => p.session === choose.value);
    const update = () => {
      const p = selected(); const owner = p?.by === `agent:${connection.id}`;
      role.disabled = save.disabled = owner || !p;
      role.value = p?.grants?.[`agent:${connection.id}`] ?? '';
      status.textContent = owner ? 'This connection created the Project. Disconnect it to revoke its credential.' : 'Access applies only to this Project.';
    };
    choose.onchange = update;
    save.onclick = async () => {
      const p = selected(); if (!p) return;
      const selectedRole = role.value;
      choose.disabled = role.disabled = save.disabled = true;
      try {
        const response = await request('/api/agent-project-access', { method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'agent-connections' },
          body: JSON.stringify({ session: p.session, connection: connection.id, role: selectedRole || null }) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.says ?? 'Project access was not saved.');
        p.grants = { ...p.grants, [`agent:${connection.id}`]: selectedRole };
        status.textContent = selectedRole ? `${connection.label} now has ${selectedRole} access to ${p.name || p.session}.` : `Access to ${p.name || p.session} revoked. Earlier work stays attributed to its author.`;
      } catch (error) { status.textContent = error instanceof Error ? error.message : 'Project access was not saved.'; }
      finally { choose.disabled = role.disabled = save.disabled = false; }
    };
    panel.append(choose, role, explain, save); update();
  } catch (error) { status.textContent = error instanceof Error ? error.message : 'Project access could not be read.'; }
}
