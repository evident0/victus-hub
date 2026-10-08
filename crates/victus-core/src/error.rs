/// Recoverable failure reported on the daemon socket as `ERR\t<message>`.
#[derive(Debug, thiserror::Error, Clone, PartialEq, Eq)]
#[error("{0}")]
pub struct HubError(pub String);

impl HubError {
    pub fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl From<String> for HubError {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for HubError {
    fn from(value: &str) -> Self {
        Self(value.to_owned())
    }
}

pub type HubResult<T> = Result<T, HubError>;
