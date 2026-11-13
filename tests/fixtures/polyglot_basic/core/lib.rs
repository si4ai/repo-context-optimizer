//! Core types for the job engine.

use std::fmt;
pub use crate::queue::{Queue, Item};

/// A job id.
pub type JobId = u64;

/// Maximum queued jobs.
pub const MAX_JOBS: usize = 64;

/// One job.
#[derive(Debug, Clone)]
pub struct Job<'a> {
    pub id: JobId,
    pub name: &'a str,
    secret: String,
}

/// Job states.
pub enum State {
    Queued,
    Running { pid: u32 },
    Done(i32),
}

/// Something that can run.
pub trait Runner {
    type Output;
    fn run(&self, job: &Job) -> Result<Self::Output, String>;
}

impl<'a> Job<'a> {
    /// Make a job.
    pub fn new(id: JobId, name: &'a str) -> Self {
        let s = "}"; let c = '{';
        Job { id, name, secret: String::new() }
    }
    fn hidden(&self) {}
}

impl fmt::Display for State {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { write!(f, "{}", 1) }
}

/* nested /* comment */ still comment { */
pub async fn spawn<T: Runner>(r: T) -> Option<T::Output> where T: Send { None }

fn private_fn() {}
