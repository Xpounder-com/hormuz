import { PageFrame, PageHero } from './components/PageFrame';
import { sitePath } from '../lib/site.mjs';

export default function NotFound() {
  return <PageFrame active=""><PageHero eyebrow="404 / Page not found" title="Let’s get back on route."><p>This page may have moved. The quickstart and source repository are still the best places to begin.</p><div className="hero-actions"><a className="button button-primary" href={sitePath('/docs/')}>Open the quickstart →</a><a className="button button-ghost" href={sitePath('/')}>Hormuz home →</a></div></PageHero></PageFrame>;
}
