xattr 0.2.3 from crates.io, MIT/Apache-2.0. GPUI 0.2.2 pulls this through async-tar.
Only src/sys/mod.rs is changed: Linux/Android use ENODATA for missing attributes.
Recent libc removed their deprecated ENOATTR alias. macOS/BSD retain ENOATTR.
Reference: https://github.com/rust-lang/libc/issues/1356
