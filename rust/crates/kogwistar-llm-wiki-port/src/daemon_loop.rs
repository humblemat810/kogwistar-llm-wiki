// T5.15 daemon / projection / maintenance lifecycle loops - mirroring Python daemon.py
use std::sync::atomic::{AtomicBool, Ordering};

/// Mirrors Python daemon loop base: single-threaded polling loop with a thread-safe stop signal.
#[derive(Debug, Default)]
pub struct DaemonLoop {
    pub poll_interval_secs: f64,
    running: AtomicBool,
    max_iterations: Option<usize>,
}

impl Clone for DaemonLoop {
    fn clone(&self) -> Self {
        Self {
            poll_interval_secs: self.poll_interval_secs,
            running: AtomicBool::new(self.running.load(Ordering::Acquire)),
            max_iterations: self.max_iterations,
        }
    }
}

impl DaemonLoop {
    pub fn new(poll_interval_secs: f64) -> Self {
        Self {
            poll_interval_secs,
            running: AtomicBool::new(false),
            max_iterations: None,
        }
    }

    pub fn with_max_iterations(mut self, max: usize) -> Self {
        self.max_iterations = Some(max);
        self
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::Acquire)
    }

    /// Mirrors Python `stop()`: thread-safe, sets stop flag.
    pub fn stop(&self) {
        self.running.store(false, Ordering::Release);
    }

    /// Mirrors Python `start()`: clears stop flag so loop may run.
    pub fn start(&self) {
        self.running.store(true, Ordering::Release);
    }

    /// Run one bounded poll pass while running. Returns iteration count.
    /// Used for tests without spawning real threads or sleeping.
    pub fn run_pass<F>(&self, mut poll: F) -> usize
    where
        F: FnMut() -> bool,
    {
        let mut count = 0usize;
        self.start();
        while self.is_running() {
            if !poll() {
                break;
            }
            count += 1;
            if let Some(max) = self.max_iterations {
                if count >= max {
                    break;
                }
            }
        }
        self.stop();
        count
    }
}

/// Mirrors Python `MaintenanceDaemon` (grouped maintenance daemon runtime).
#[derive(Clone, Debug, Default)]
pub struct MaintenanceDaemon {
    pub inner: DaemonLoop,
}

impl MaintenanceDaemon {
    pub fn new(poll_interval_secs: f64) -> Self {
        Self {
            inner: DaemonLoop::new(poll_interval_secs),
        }
    }
    pub fn start(&self) {
        self.inner.start();
    }
    pub fn stop(&self) {
        self.inner.stop();
    }
    pub fn is_running(&self) -> bool {
        self.inner.is_running()
    }
}

/// Mirrors Python `ProjectionDaemon`.
#[derive(Clone, Debug, Default)]
pub struct ProjectionDaemon {
    pub inner: DaemonLoop,
}

impl ProjectionDaemon {
    pub fn new(poll_interval_secs: f64) -> Self {
        Self {
            inner: DaemonLoop::new(poll_interval_secs),
        }
    }
    pub fn start(&self) {
        self.inner.start();
    }
    pub fn stop(&self) {
        self.inner.stop();
    }
    pub fn is_running(&self) -> bool {
        self.inner.is_running()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn daemon_loop_starts_and_stops() {
        let loop_ = DaemonLoop::new(10.0);
        assert!(!loop_.is_running());
        loop_.start();
        assert!(loop_.is_running());
        loop_.stop();
        assert!(!loop_.is_running());
    }

    #[test]
    fn run_pass_terminates_on_poll_false() {
        let loop_ = DaemonLoop::new(10.0);
        let mut calls = 0usize;
        let count = loop_.run_pass(|| {
            calls += 1;
            calls < 3
        });
        assert_eq!(count, 2);
        assert!(!loop_.is_running());
    }

    #[test]
    fn run_pass_respects_max_iterations() {
        let loop_ = DaemonLoop::new(10.0).with_max_iterations(5);
        let count = loop_.run_pass(|| true);
        assert_eq!(count, 5);
        assert!(!loop_.is_running());
    }

    #[test]
    fn maintenance_and_projection_daemon_stop() {
        let maint = MaintenanceDaemon::new(10.0);
        let proj = ProjectionDaemon::new(10.0);
        maint.start();
        proj.start();
        assert!(maint.is_running());
        assert!(proj.is_running());
        maint.stop();
        proj.stop();
        assert!(!maint.is_running());
        assert!(!proj.is_running());
    }
}
