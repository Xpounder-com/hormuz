'use client';
import { useState } from 'react';
import { customerQuestions } from '../../lib/customer-questions.mjs';
import { sitePath } from '../../lib/site.mjs';

export function QuestionIndex({ compact = false }: { compact?: boolean }) {
  const [query, setQuery] = useState('');
  const [group, setGroup] = useState('All questions');
  const groups = ['All questions', 'Setup', 'Policy', 'Analytics', 'Context', 'Compatibility', 'Billing'];
  const matches = customerQuestions.filter(([category, question, answer]) => (group === 'All questions' || category === group) && `${question} ${answer}`.toLowerCase().includes(query.toLowerCase()));
  const shown = compact && !query && group === 'All questions' ? matches.filter((_, index) => [0,6,12,19,25,29].includes(index)) : matches;
  return <section className="question-index" id="faq" aria-labelledby="questions-title"><p className="landing-eyebrow">ANSWERS BEFORE YOU INSTALL</p><h2 id="questions-title">Would this work for your team?</h2><p>Look up a practical question, or go straight to the example that answers it.</p><label className="question-search">Search {customerQuestions.length} questions<input type="search" placeholder="Try budgets, Ollama, setup, or privacy" value={query} onChange={event => setQuery(event.target.value)} /></label><div className="question-groups" aria-label="Filter questions">{groups.map(category => <button type="button" key={category} aria-pressed={category === group} onClick={() => setGroup(category)}>{category}</button>)}</div><p className="question-count" role="status">{shown.length} questions shown{compact && !query && group === 'All questions' ? ` · ${customerQuestions.length} searchable answers` : ''}</p>{shown.map(([category, question, answer, href]) => <details key={question}><summary>{question}</summary><p>{answer} <a href={sitePath(href)}>Explore {category.toLowerCase()} →</a></p></details>)}{shown.length === 0 && <p>No matching question. Try a shorter term or <a href={sitePath('/contact/?interest=review')}>ask Mehrdad</a>.</p>}{compact && <p><a href={sitePath('/demo/#faq')}>See all {customerQuestions.length} practical answers →</a></p>}</section>;
}
