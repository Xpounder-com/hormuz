#!/bin/zsh

set -euo pipefail
set +x

readonly repository="Xpounder-com/hormuz"
readonly environment_name="v1-release-custody"
readonly secret_name="V1_RELEASE_PUBLISH_TOKEN"

authorized_steward="$(env -u GH_TOKEN -u GITHUB_TOKEN gh api \
  "repos/${repository}/actions/variables/V1_RELEASE_STEWARD" \
  --jq .value)" || {
  print -u2 "Could not resolve the authorized steward with local GitHub authentication."
  exit 2
}

clear_publisher_token() {
  publisher_token=""
  unset publisher_token
}

trap clear_publisher_token EXIT

if [[ ! -t 0 || ! -t 1 ]]; then
  print -u2 "This helper must run in an interactive terminal."
  exit 2
fi

print "Paste the fresh freeze-publisher token, then press Return."
print "Input is hidden and will be sent only to GitHub's protected environment secret."
IFS= read -r -s publisher_token
print

if [[ -z "${publisher_token}" ]]; then
  print -u2 "No token was entered; the GitHub secret was not changed."
  exit 2
fi

if [[ "${publisher_token}" == *[[:space:]]* ]]; then
  print -u2 "The token contained whitespace; the GitHub secret was not changed."
  exit 2
fi

if [[ "${publisher_token}" != github_pat_* ]]; then
  print -u2 "The input is not a fine-grained GitHub personal access token."
  print -u2 "The GitHub secret was not changed."
  exit 2
fi

publisher_actor="$(GH_TOKEN="${publisher_token}" gh api user --jq .login 2>/dev/null)" || {
  print -u2 "The publisher token did not authenticate; the GitHub secret was not changed."
  exit 2
}

if [[ "${publisher_actor}" != "${authorized_steward}" ]]; then
  print -u2 "The publisher token belongs to the wrong steward; the GitHub secret was not changed."
  exit 2
fi

publisher_repository="$(GH_TOKEN="${publisher_token}" gh api \
  "repos/${repository}" --jq .full_name 2>/dev/null)" || {
  print -u2 "The publisher token cannot access the Hormuz repository; the GitHub secret was not changed."
  exit 2
}

if [[ "${publisher_repository}" != "${repository}" ]]; then
  print -u2 "The publisher token resolved the wrong repository; the GitHub secret was not changed."
  exit 2
fi

publisher_release_shape="$(GH_TOKEN="${publisher_token}" gh api \
  "repos/${repository}/releases?per_page=1" --jq type 2>/dev/null)" || {
  print -u2 "The publisher token cannot inspect the release namespace; the GitHub secret was not changed."
  exit 2
}

if [[ "${publisher_release_shape}" != "array" ]]; then
  print -u2 "The publisher token received an invalid release response; the GitHub secret was not changed."
  exit 2
fi

publisher_releases_before="$(GH_TOKEN="${publisher_token}" gh api --paginate \
  "repos/${repository}/releases?per_page=100" \
  --jq '.[] | [.id, .tag_name, .draft, .prerelease, .updated_at] | @json' \
  2>/dev/null)" || {
  print -u2 "Could not snapshot the release namespace; the GitHub secret was not changed."
  exit 2
}

publisher_probe_status="$(
  PUBLISHER_REPOSITORY="${repository}" \
  PUBLISHER_TOKEN="${publisher_token}" \
  /usr/bin/python3 - <<'PY'
import os
import sys
import urllib.error
import urllib.request


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


repository = os.environ["PUBLISHER_REPOSITORY"]
token = os.environ["PUBLISHER_TOKEN"]
request = urllib.request.Request(
    f"https://api.github.com/repos/{repository}/releases",
    data=b"{}",
    method="POST",
    headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "hormuz-v1-publisher-preflight",
        "X-GitHub-Api-Version": "2022-11-28",
    },
)

try:
    with urllib.request.build_opener(NoRedirects()).open(request, timeout=15) as response:
        status = response.status
except urllib.error.HTTPError as error:
    status = error.code
except Exception:
    sys.exit(2)

print(status)
PY
)" || {
  print -u2 "The publisher release-write probe failed; the GitHub secret was not changed."
  exit 2
}

if [[ "${publisher_probe_status}" != "422" ]]; then
  print -u2 "The publisher token lacks effective release-write authority; the GitHub secret was not changed."
  exit 2
fi

publisher_releases_after="$(GH_TOKEN="${publisher_token}" gh api --paginate \
  "repos/${repository}/releases?per_page=100" \
  --jq '.[] | [.id, .tag_name, .draft, .prerelease, .updated_at] | @json' \
  2>/dev/null)" || {
  print -u2 "Could not recheck the release namespace; the GitHub secret was not changed."
  exit 2
}

if [[ "${publisher_releases_after}" != "${publisher_releases_before}" ]]; then
  print -u2 "The release namespace changed during validation; the GitHub secret was not changed."
  exit 2
fi

print "Publisher token authenticated with release-write authority for ${authorized_steward} and ${repository}."

# gh reads the secret from stdin only when --body is omitted. Passing
# "--body -" stores a literal hyphen and must never be used here.
if print -rn -- "${publisher_token}" | env -u GH_TOKEN -u GITHUB_TOKEN \
  gh secret set "${secret_name}" \
  --repo "${repository}" \
  --env "${environment_name}"; then
  print "Publisher secret updated. You may close this terminal."
else
  status=$?
  print -u2 "GitHub rejected the secret update."
  exit "${status}"
fi
