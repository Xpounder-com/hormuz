use crate::{Counts, Error, TokenCounter};
use base64::{engine::general_purpose::STANDARD, Engine};
use rustc_hash::FxHashMap;
use sha2::{Digest, Sha256};
use std::{fs, io::Read, path::Path};
use tiktoken_rs::CoreBPE;

pub const RESOURCE_VERSION: &str = "0.14.0";
pub const RESOURCE_FILES: [(&str, &str, &str); 2] = [
    (
        "cl100k_base",
        "9b5ad71b2ce5302211f9c61530b329a4922fc6a4",
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    ),
    (
        "o200k_base",
        "fb374d419588a4632f3f557e76b4b70aebbca790",
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    ),
];
const MAX_RESOURCE_BYTES: u64 = 8 * 1024 * 1024;
const CL100K_PATTERN: &str = r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s";

fn resource(directory: &Path, file: &str, expected: &str) -> Result<Vec<u8>, Error> {
    let metadata = directory
        .symlink_metadata()
        .map_err(|_| Error::ResourcesUnavailable)?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(Error::ResourcesUnavailable);
    }
    let path = directory.join(file);
    let metadata = path
        .symlink_metadata()
        .map_err(|_| Error::ResourcesUnavailable)?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.len() == 0
        || metadata.len() > MAX_RESOURCE_BYTES
    {
        return Err(Error::ResourcesUnavailable);
    }
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|_| Error::ResourcesUnavailable)?
        .take(MAX_RESOURCE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| Error::ResourcesUnavailable)?;
    if bytes.len() as u64 > MAX_RESOURCE_BYTES
        || format!("{:x}", Sha256::digest(&bytes)) != expected
    {
        return Err(Error::ResourcesUnavailable);
    }
    Ok(bytes)
}

/// Readiness intentionally does not construct a BPE or open the network.
pub fn validate_resources(directory: &Path) -> Result<(), Error> {
    for (_, file, hash) in RESOURCE_FILES {
        resource(directory, file, hash)?;
    }
    Ok(())
}

pub struct Tokenizers {
    encodings: Vec<(&'static str, CoreBPE)>,
}
impl Tokenizers {
    pub fn load(directory: &Path) -> Result<Self, Error> {
        let mut encodings = Vec::new();
        for (name, file, hash) in RESOURCE_FILES {
            let bytes = resource(directory, file, hash)?;
            let text = std::str::from_utf8(&bytes).map_err(|_| Error::ResourcesUnavailable)?;
            let mut ranks = FxHashMap::default();
            for line in text.lines() {
                let (encoded, rank) = line.split_once(' ').ok_or(Error::ResourcesUnavailable)?;
                let token = STANDARD
                    .decode(encoded)
                    .map_err(|_| Error::ResourcesUnavailable)?;
                let rank = rank
                    .parse::<u32>()
                    .map_err(|_| Error::ResourcesUnavailable)?;
                if ranks.insert(token, rank).is_some() {
                    return Err(Error::ResourcesUnavailable);
                }
            }
            let pattern = if name == "cl100k_base" {
                CL100K_PATTERN
            } else {
                tiktoken_rs::O200K_BASE_PAT_STR
            };
            let encoding = CoreBPE::new(ranks, FxHashMap::default(), pattern)
                .map_err(|_| Error::ResourcesUnavailable)?;
            encodings.push((name, encoding));
        }
        Ok(Self { encodings })
    }
}
impl TokenCounter for Tokenizers {
    fn count(&self, text: &str) -> Result<Counts, Error> {
        if text.len() > crate::MAX_REQUEST_BYTES {
            return Err(Error::LimitExceeded);
        }
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            self.encodings
                .iter()
                .map(|(name, encoding)| ((*name).to_owned(), encoding.count_ordinary(text)))
                .collect()
        }))
        .map_err(|_| Error::ResourcesUnavailable)
    }
}
