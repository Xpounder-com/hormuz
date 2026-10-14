import catalog from './skill-catalog.json' with { type: 'json' };

export type Skill = {
  id: string;
  name: string;
  command: string;
  description: string;
  version: 1;
  instructions: string;
};
export const skills = catalog as Skill[];

// Pure instruction selection. It grants no tools, filesystem access or model calls.
export function activeSkills(allowed: string[] = [], prompt: string): Skill[] {
  const command = prompt.trim().split(/\s/, 1)[0].toLowerCase();
  return skills.filter((skill) => allowed.includes(skill.id) && command === skill.command);
}
