---
name: diagnose-crash
description: Analyse an application crash from systemd-coredump — read the backtrace, determine the likely cause and propose a fix. Use when handed a crash report file or when the user asks why something crashed.
---

# Crash diagnosis

You have been given a `coredumpctl` report for a crashed process. Work through it:

## 1. Establish the facts
Process name, signal (SIGSEGV = invalid memory access, SIGABRT = the program
aborted itself, usually an assert or uncaught exception), package version, time.

## 2. Read the backtrace
Find the **first frame in the application's own code** — frames in libc or Qt
are where the failure surfaced, not where it originated. If frames show `n/a`,
debug symbols are missing: say so and name the `-debug`/`-dbgsym` package.

## 3. Get context
- package version and install date (`pacman -Qi`, `dpkg -s`, `rpm -qi`)
- `journalctl -b` around the crash time — what happened right before
- first time, or recurring? (`coredumpctl list <process>`)

## 4. Classify
- **known upstream bug** — search the web for the process name + top frames
- **configuration conflict** — typically right after an update, a third-party plugin or theme
- **hardware** — repeated crashes across unrelated processes; suggest memory and disk checks

## 5. Conclude
Briefly: what crashed, why, what to do. Link the upstream issue if there is one.
Do not propose reinstalling the system as a first step.

## Reporting upstream
Only when you have **confirmed** the cause and it is not local configuration.
Offer to draft the report — never send one without the user's approval.
