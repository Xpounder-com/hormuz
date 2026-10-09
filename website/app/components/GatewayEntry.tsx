'use client';

import { useEffect, useState } from 'react';
import { workAcquisitionLabels, workAcquisitionLink } from '../../lib/workspace.mjs';

export function GatewayEntry({ workUrl }: { workUrl: string }) {
  const [search, setSearch] = useState('');
  const [includeSource, setIncludeSource] = useState(false);
  useEffect(() => { setSearch(window.location.search); }, []);
  const labels = workAcquisitionLabels(search);
  const hasLabels = Object.keys(labels).length > 0;
  return <div className="gateway-entry">
    <a className="button landing-primary" href={workAcquisitionLink(workUrl, search, includeSource)} rel="noreferrer">Open AI Work <span aria-hidden="true">→</span></a>
    {hasLabels && <div className="work-entry-attribution"><label className="checkbox-label"><input type="checkbox" checked={includeSource} onChange={event => setIncludeSource(event.target.checked)} />Include these campaign labels in my gateway handoff.</label><p>{Object.entries(labels).map(([key, value]) => `${key.replace('utm_', '')}: ${value}`).join(' · ')}</p><p>Your gateway asks you to confirm the labels before linking them to private qualification and activation progress. This does not change your website analytics preferences. No credentials or work identifiers are attached.</p></div>}
  </div>;
}
