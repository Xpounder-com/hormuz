use std::ffi::OsString;
use std::path::PathBuf;

pub struct Options {
    pub smoke: bool,
    pub state_directory: Option<PathBuf>,
}

impl Options {
    pub fn parse(arguments: Vec<OsString>) -> Result<Self, ()> {
        let mut options = Self {
            smoke: false,
            state_directory: None,
        };
        let mut preview = false;
        let mut arguments = arguments.into_iter();
        while let Some(argument) = arguments.next() {
            if argument == "--smoke-test" && !options.smoke {
                options.smoke = true;
            } else if argument == "--preview" && !preview {
                preview = true;
            } else if argument == "--state-directory" && options.state_directory.is_none() {
                let path = PathBuf::from(arguments.next().ok_or(())?);
                if !path.is_absolute() {
                    return Err(());
                }
                options.state_directory = Some(path);
            } else {
                return Err(());
            }
        }
        // Isolated test roots are explicit synthetic-preview inputs. Connected
        // credential coordination will always use the fixed native root.
        if options.state_directory.is_some() && !(preview || options.smoke) {
            return Err(());
        }
        Ok(options)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn parse(args: &[&str]) -> Result<Options, ()> {
        Options::parse(args.iter().map(OsString::from).collect())
    }
    #[test]
    fn synthetic_root_requires_explicit_preview_or_smoke() {
        let root = std::env::temp_dir().join("hormuz-options-only");
        assert!(Options::parse(vec![
            "--preview".into(),
            "--state-directory".into(),
            root.clone().into()
        ])
        .is_ok());
        assert!(Options::parse(vec![
            "--smoke-test".into(),
            "--state-directory".into(),
            root.clone().into()
        ])
        .is_ok());
        assert!(Options::parse(vec!["--state-directory".into(), root.into()]).is_err());
        for args in [
            &["--preview", "--state-directory", "relative"][..],
            &["--state-directory"],
            &["--preview", "--preview"],
            &["--unknown"],
        ] {
            assert!(parse(args).is_err());
        }
        assert!(parse(&[]).is_ok());
        assert!(parse(&["--smoke-test"]).unwrap().smoke);
    }
}
