// Own one retained scientific placement and its Controls subtree, never the first matching display title.
export function scientificDocument(page, title, expected) {
  const key = saved => `scientific:${saved.resource.day}:${saved.resource.id}:${saved.resource.version}:${saved.placementId}`;
  const exact = saved => page.locator(`section.study-document-artifact[data-content-key=${JSON.stringify(key(saved))}]`);
  // The canonical title is retained metadata, not the display heading. Before
  // bind this locator remains usable by callers waiting for the window to open.
  let root = expected ? exact(expected) : page.locator(`section.study-document-artifact[data-study-retained*=${JSON.stringify('"title":'+JSON.stringify(title))}]`);
  let scope, captured;
  const bound = () => {
    if (!scope) throw new Error('Bind the scientific document before using its controls');
    return scope;
  };
  return {
    get root() { return root; },
    bind: async () => {
      scope=undefined;
      if (await root.count() !== 1 || !await root.isVisible()) throw new Error(`Expected one scientific document named ${title}`);
      const saved = JSON.parse(await root.getAttribute('data-study-retained'));
      const ref = saved?.resource;
      if (saved.title !== title || !ref || typeof ref.day !== 'string' || !/^p[0-9a-f]{32}$/.test(ref.id)
          || !Number.isSafeInteger(ref.version) || ref.version < 1 || typeof saved.placementId !== 'string'
          || await root.getAttribute('data-content-key') !== `scientific:${ref.day}:${ref.id}:${ref.version}:${saved.placementId}`)
        throw new Error('The scientific document does not match its retained identity');
      const identity={resource:{day:ref.day,id:ref.id,version:ref.version,picture:ref.picture??null},placementId:saved.placementId};
      const prior=captured??expected;
      if(prior && (key(prior)!==key(identity) || (prior.resource.picture??null)!==identity.resource.picture))
        throw new Error('The scientific document changed its captured retained identity');
      captured=identity;root=exact(identity);
      // ControlDeck retains its scientific shell inside this exact owner subtree.
      // Observe its actual display label and associated launcher; never union a
      // similarly named region elsewhere on the page into scientific ownership.
      const controls=root.locator('section.capability-controls[role="region"]');
      const count=await controls.count();
      if(count>1)throw new Error('The scientific document has ambiguous Controls owners');
      if(count===1){
        const name=await controls.getAttribute('aria-label');
        if(!name?.endsWith(' controls') || await root.getByRole('button',{name:`Show ${name}`,exact:true}).count()!==1)
          throw new Error('The scientific Controls do not match their owned launcher');
      }
      scope=root;
    },
    button: (name, {includeHidden = false} = {}) => bound().getByRole('button', {name, exact:true, includeHidden}),
    label: name => bound().getByLabel(name, {exact:true}),
    tab: name => bound().getByRole('tab', {name, exact:true}),
    preview: () => root.getByLabel('Private scientific preview. Changes are not saved.', {exact:true}),
    statuses: () => bound().getByRole('status'),
  };
}
