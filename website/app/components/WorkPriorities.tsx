'use client';

import { useState } from 'react';
import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';

const policies = {
  cost: { label: 'Cost first', title: 'Prioritize lower observed work cost.', description: 'Compare captured cost estimates across comparable completed work, including retries. Eligible routes must still satisfy your spending limits and the observed completion and rework guardrails.' },
  speed: { label: 'Speed first', title: 'Prioritize faster eligible routes.', description: 'Compare observed provider attempt time across comparable completed work within your spending limits. Whole-work completion time stays a separate measurement.' },
  quality: { label: 'Outcome first', title: 'Favor observed completion with less rework.', description: 'Use comparable workflow observations to prefer higher recorded completion and less rework. Submitted checks credit their stated condition; limited evidence does not establish that a model is best.' },
};

export function WorkPriorities() {
  const [selected, setSelected] = useState<keyof typeof policies>('cost');
  const policy = policies[selected];
  return <div className="work-priorities"><div><h3>You choose the objective.</h3><p>This explanation changes locally. It does not alter a live workspace policy.</p><div className="priority-options" role="group" aria-label="Explore work priorities">{Object.entries(policies).map(([key, item]) => <button key={key} type="button" aria-pressed={selected === key} aria-controls="priority-explanation" onClick={() => setSelected(key as keyof typeof policies)}>{item.label}</button>)}</div></div><div id="priority-explanation" aria-live="polite" aria-atomic="true"><h3>{policy.title}</h3><p>{policy.description}</p><CampaignLink className="text-link" href={sitePath('/work/')}>Open your configured AI Work gateway →</CampaignLink></div></div>;
}
