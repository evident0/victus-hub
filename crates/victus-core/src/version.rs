/// Package version. Keep this aligned with the workspace version.
pub const PROGRAM_VERSION: &str = "1.0.3";

/// Compare stable release tags such as `1.0.2` and `v1.0.2` numerically.
pub fn release_is_newer(tag: &str, installed: &str) -> Result<bool, super::HubError> {
    Ok(version_parts(tag)? > version_parts(installed)?)
}

fn version_parts(value: &str) -> Result<(u64, u64, u64), super::HubError> {
    let value = value.trim();
    let body = value.strip_prefix(['v', 'V']).unwrap_or(value);
    let mut parts = body.split('.');
    let parse = |part: Option<&str>| -> Result<u64, super::HubError> {
        let part = part.ok_or_else(|| super::HubError::new(format!("Invalid release version: {value:?}")))?;
        if part.is_empty() || !part.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(super::HubError::new(format!("Invalid release version: {value:?}")));
        }
        part.parse()
            .map_err(|_| super::HubError::new(format!("Invalid release version: {value:?}")))
    };
    let major = parse(parts.next())?;
    let minor = parse(parts.next())?;
    let patch = parse(parts.next())?;
    if parts.next().is_some() {
        return Err(super::HubError::new(format!("Invalid release version: {value:?}")));
    }
    Ok((major, minor, patch))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn newer_tag_compares_numerically() {
        assert!(release_is_newer("v1.2.0", "1.0.3").unwrap());
        assert!(!release_is_newer("1.0.3", "v1.0.3").unwrap());
        assert!(release_is_newer("1.0.10", "1.0.9").unwrap());
        assert!(release_is_newer("1.10.0", "1.9.9").unwrap());
    }

    #[test]
    fn rejects_non_release_tags() {
        assert!(release_is_newer("main", "1.0.3").is_err());
        assert!(release_is_newer("1.0", "1.0.3").is_err());
    }
}
