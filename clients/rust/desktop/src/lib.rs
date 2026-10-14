//! Shared credential-free desktop worker and display projection. Native shells
//! supply custody, browser dispatch and a nonblocking notification callback.
#![forbid(unsafe_code)]

pub mod connection;
pub mod presentation;
