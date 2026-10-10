import { readFileSync, writeFileSync } from 'node:fs';
import { customerQuestions } from '../lib/customer-questions.mjs';
import { siteUrl } from '../lib/site.mjs';

const output = new URL('../public/demo/walkthrough.txt', import.meta.url);
const marker = '\nPRACTICAL QUESTIONS\n';
const current = readFileSync(output, 'utf8');
if (!current.includes(marker)) throw new Error('The walkthrough is missing its FAQ section.');
const introduction = current.slice(0, current.indexOf(marker));
const questions = customerQuestions.map(([category, question, answer, href]) =>
  `${category}: ${question}\n${answer}\n${siteUrl(href)}`,
).join('\n\n');
writeFileSync(output, `${introduction}${marker}\n${questions}\n`);
console.log(`Updated the plain-text walkthrough with ${customerQuestions.length} current answers.`);
