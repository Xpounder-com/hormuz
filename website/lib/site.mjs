export const BASE_PATH = '/hormuz';
export const SITE_ORIGIN = 'https://xpounder-com.github.io';
export const REPOSITORY = 'https://github.com/Xpounder-com/hormuz';
export const AUTHOR = 'Mehrdad Zaker';
export const CONTACT_EMAIL = 'zaker.mehrdad@gmail.com';
export const SOURCE_VERSION = 'v1.0.0';
export const OCI_VERSION = 'v0.1.3';

/** Native anchors need the GitHub project prefix; Next's basePath alone is not enough. */
export function sitePath(path = '/') {
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Expected a local absolute path');
  return `${BASE_PATH}${path}`;
}

export function siteUrl(path = '/') {
  return `${SITE_ORIGIN}${sitePath(path)}`;
}

export function sourcePath(path) {
  return `${REPOSITORY}/blob/main/${path}`;
}
