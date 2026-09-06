"use client";

import { useEffect, useState, type ComponentProps } from 'react';
import { campaignLink } from '../../lib/lead.mjs';

/** Pass bounded campaign tags to the application without cookies or storage. */
export function CampaignLink({ href, children, ...props }: ComponentProps<'a'> & { href: string }) {
  const [destination, setDestination] = useState(href);
  useEffect(() => { setDestination(campaignLink(href, window.location.search)); }, [href]);
  return <a {...props} href={destination}>{children}</a>;
}
