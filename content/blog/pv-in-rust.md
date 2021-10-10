---
title: "Small pv clone in under 75 lines of Rust"
date: 2021-10-10T17:44:31+02:00
tags: ["rust", "linux"]
summary: "Finding out how pv works and writing a small clone in Rust"
---

## What is `pv`?

[`pv`](https://man7.org/linux/man-pages/man1/pv.1.html), likely for _pipe view_, is a neat little Linux command that let's us monitor the amount of data that goes through a pipe.

```shell
❯ pv ~/Pictures/some_picture.jpg > some_picture.jpg
2.90MiB 0:00:00 [ 157MiB/s] [===========================================>] 100%

❯ pv ~/Pictures/some_picture.jpg | ssh remote.machine "cat > some_picture.jpg"
2.90MiB 0:00:03 [ 788KiB/s] [===========================================>] 100%
```

`pv` moves data from its standard input to its standard output (or in this case from a file) while measuring how much data is moved and how fast.  
It can sometimes be really fast. A quick test to see how fast it can go is to use the [`yes`](https://man7.org/linux/man-pages/man1/yes.1.html) command to generate input and then output the data to `/dev/null`. On my laptop I can get `5-6GiB/s`, but this throughput will [vary widly from machine to machine](https://www.reddit.com/r/unix/comments/6gxduc/how_is_gnu_yes_so_fast/), and the bottleneck could be either `yes`, `pv` or the way linux is configured on my machine.

```shell
❯ yes | pv > /dev/null
35.4GiB 0:00:06 [5.60GiB/s] [      <=>                                   ]
```

## How does `pv` work?

Copying some input data to the output while keeping information to report seems somewhat straight forward at first, so we will figure out the details as we go. 🤞  
But what about the report? How can `pv` output anything to my terminal if its output is being piped to the next command/a file? According to the [man page](https://man7.org/linux/man-pages/man1/pv.1.html) it uses standard error!  
The report that `pv` outputs purposefully spans only 1 line. This way, the report can be updated in place by using the carriage return character `'\r'` without using a linefeed character `'\n'` to go back to the beginning of the line and overwrite what was previously written. Smart! Only downside is that it needs to write enough the second time around to fully overwrite what was previously written.

## A naive implementation

Let's implement `pv` in Rust for a bit of fun and to learn along the way!

In this first iteration we will setup a buffer. We continuously read from standard input into that buffer then write from that buffer to standard output.

```rust
// main.rs
use std::io;
use std::io::prelude::*;

// This buffer size seems optimal on my machine
const BUFFER_SIZE: usize = 8 * 1024;

fn main() -> io::Result<()> {
    // Get handles for standard input and standard output
    let mut stdin = io::stdin();
    let mut stdout = io::stdout();

    // Setup a buffer to transfer data from stdin to stdout
    let mut buffer = [0; BUFFER_SIZE];

    loop {
        // Read data from the standard input into the buffer
        let bytes = stdin.read(&mut buffer)?;
        if bytes == 0 {
            // No more data to read, return successfully
            return Ok(());
        }

        // Write the data we've just read from the buffer to standard output
        //
        // Note: we use `write_all` instead of `write` as it could take several
        // writes to finish depending on how busy the recipient is
        stdout.write_all(&buffer[..bytes])?;
    }
}
```

Let's try it:

```shell
❯ echo "Hello" | cargo run --release --quiet --bin rpv
Hello
```

Great! 🎉  
We have data passing through. Though, it's a bit useless at the moment 😅

Let's add some reporting. For similar functionality to `pv` we need to keep track of how many bytes have been transferred, as well as the time elapsed since the beginning. From that we can get the average throughput since the start of the program.

How often should we report progress? If we do it on every transfer of one buffer we are likely to slow down the transfer as well as making the report unreadable. We will therefore only report once every second.

```rust
// main.rs
/* ... */
use std::time::{Duration, Instant};

/* ... */
const REPORT_PERIOD: f64 = 1.0;

/* fn main() -> ... */
    // Keep track of how many bytes are being transferred as we go
    let mut bytes_so_far = 0;

    // Start timer to figure out the elapsed time
    let start_time = Instant::now();
    let mut next_report_time = start_time;

    /* loop ... */
        // Update what we have transferred so far
        bytes_so_far += bytes;

        // Report if it is time to do so
        let now = Instant::now();
        if now >= next_report_time {
            next_report_time = now + Duration::from_secs_f64(REPORT_PERIOD);
            report(bytes_so_far, start_time.elapsed());
        }
```

To print the report we will use standard error and the `'\r'` trick discussed. To make reporting nicer we will make use of the great [`byte-unit`](https://crates.io/crates/byte-unit) crate to properly format the byte count and throughput in a human readable way.

```rust
fn report(byte_count: usize, elapsed: Duration) {
    // Use the byte_unit crate to do all the unit conversions and display logic
    use byte_unit::{Byte, ByteUnit};

    let adjusted_byte_count = Byte::from_unit(byte_count as f64, ByteUnit::B)
        .unwrap()
        .get_appropriate_unit(true);

    // Get the average throughput since the start
    let throughput = byte_count as f64 / elapsed.as_secs_f64();
    let adjusted_throughput = Byte::from_unit(throughput, ByteUnit::B)
        .unwrap()
        .get_appropriate_unit(true);

    // Print report to standard error
    // We use some padding to make the number of characters outputted stable so
    // that the carriage return trick properly overwrites all previous output
    eprint!(
        "{:>10} | {:>10}/s | {:>10}\r",
        adjusted_byte_count.to_string(),
        adjusted_throughput.to_string(),
        // Debug for Duration doesn't pad properly, so format beforehand
        format!("{:.1?}", elapsed)
    );
}
```

So how well does this run?

```shell
❯ yes | cargo run --release --quiet --bin rpv > /dev/null
  7.54 GiB |   4.32 GiB/s |      11.0s
```

Not bad for a first iteration. I did have to tweak the buffer size in order to get the best throughput possible on my machine, but we're getting close to the throughput we saw with `pv`.

## How is `pv` so fast?

In order to find out how `pv` is so fast, we should have a look at what kind of IO it does in its main loop. Then we can compare that to what we are doing.  
[`strace`](https://man7.org/linux/man-pages/man1/strace.1.html) is an amazing tool to get exactly this type of information. You run `strace command` and it prints all of the system calls the command does, which is what IO is: a bunch of system calls to get the Linux kernel to do some work for you.

In our case, it is slightly trickier to get this info since `pv` already makes heavy use of the standard output (and standard error) so it's not as straight forward to call `strace` on `pv`. Fortunately we can use `strace` by providing it the `PID` of the program we are interested in.

So we start out command in one terminal:

```shell
❯ yes | pv > /dev/null
```

And with a bit of `bash` magic we call `strace` on that `pv` (assuming there's only one instance of `pv` running):

```shell
❯ strace -p $(ps aux | grep "pv$" | tr -s ' ' | cut -d' ' -f2)
strace: Process 176755 attached
select(1, [0], [], NULL, {tv_sec=0, tv_usec=90000}) = 1 (in [0], left {tv_sec=0, tv_usec=89999})
splice(0, NULL, 1, NULL, 131072, SPLICE_F_MORE) = 65536
select(1, [0], [], NULL, {tv_sec=0, tv_usec=90000}) = 1 (in [0], left {tv_sec=0, tv_usec=89999})
splice(0, NULL, 1, NULL, 131072, SPLICE_F_MORE) = 65536
select(1, [0], [], NULL, {tv_sec=0, tv_usec=90000}) = 1 (in [0], left {tv_sec=0, tv_usec=89999})
...
<detached ...>
```

If we compare it to our version (`rpv`):

```shell
❯ strace -p $(ps aux | grep "rpv$" | tr -s ' ' | cut -d' ' -f2)
strace: Process 183150 attached
read(0, "y\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\n"..., 8192) = 8192
write(1, "y\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\n"..., 8192) = 8192
read(0, "y\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\n"..., 8192) = 8192
write(1, "y\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\n"..., 8192) = 8192
read(0, "y\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\ny\n"..., 8192) = 8192
...
<detached ...>
```

It looks like `pv` uses a different strategy for moving data around. The relevant system call it uses is `splice`, for which the [`man page`](https://man7.org/linux/man-pages/man2/splice.2.html) tells us:

> **splice**() moves data between two file descriptors without copying between kernel address space and user address space.

So that's the trick! We can make the data go from one file to another without copying the data via user space (our program).  
`pv` since to be moving data with a chunk size of `65536` bytes (even though it is requesting `131072`). This is a bit bigger than our buffer of `8192` bytes, which means that `pv` uses fewer system calls than we do to move the same amount of data as well as not needing to copy it.

The `select` system call looks to be waiting for standard input (file descriptor `0`) to have some input available. This is likely to prevent spamming `splice` system calls when no data is available and no progress can be made anyway. But as far as I can tell, when using `splice` with a pipe on the input, it patiently waits for data to become available.

## Using `splice` ourselves

The simplest way for us to call the `splice` system call is to use the [`nix`](https://crates.io/crates/nix) crate.  
Changing our code to use [`nix::fcntl::splice`](https://docs.rs/nix/0.23.0/nix/fcntl/fn.splice.html) instead of `read`/`write` is fairly straight forward:

```rust
// main.rs
use nix::fcntl::{splice, SpliceFFlags};
use std::os::unix::io::AsRawFd;
/* ... */

const CHUNK_SIZE: usize = 64 * 1024;
/* ... */

/* fn main() -> ... */

    /* loop ... */
        // Move data from stdin to stdout in kernel space
        let bytes = splice(
            stdin.as_raw_fd(),
            None,
            stdout.as_raw_fd(),
            None,
            CHUNK_SIZE,
            SpliceFFlags::SPLICE_F_MOVE | SpliceFFlags::SPLICE_F_MORE,
        )?;

        /* ... */
```

Let's run it:

```shell
❯ yes | cargo run --release --quiet --bin rpv > /dev/null
 96.28 GiB |   7.41 GiB/s |      13.0s
```

Success! 🎉  
It even looks a bit faster than `pv`, maybe because we've omitted these `select` system calls?

```shell
❯ strace -p $(ps aux | grep "rpv$" | tr -s ' ' | cut -d' ' -f2)
strace: Process 221492 attached
splice(0, NULL, 1, NULL, 65536, SPLICE_F_MOVE|SPLICE_F_MORE) = 65536
splice(0, NULL, 1, NULL, 65536, SPLICE_F_MOVE|SPLICE_F_MORE) = 65536
splice(0, NULL, 1, NULL, 65536, SPLICE_F_MOVE|SPLICE_F_MORE) = 65536
splice(0, NULL, 1, NULL, 65536, SPLICE_F_MOVE|SPLICE_F_MORE) = 65536
...
<detached ...>
```

## Under 75 lines

We did it, we have a basic version of `pv` in Rust in under 75 lines.

```rust
use nix::fcntl::{splice, SpliceFFlags};
use std::io;
use std::os::unix::io::AsRawFd;
use std::time::{Duration, Instant};

const CHUNK_SIZE: usize = 64 * 1024;
const REPORT_PERIOD: f64 = 1.0;

fn main() -> io::Result<()> {
    // Get handles for standard input and standard output
    let stdin = io::stdin();
    let stdout = io::stdout();

    // Keep track of how many bytes are being transferred as we go
    let mut bytes_so_far = 0;

    // Start timer to figure out the elapsed time
    let start_time = Instant::now();
    let mut next_report_time = start_time;

    loop {
        // Move data from stdin to stdout in kernel space
        let bytes = splice(
            stdin.as_raw_fd(),
            None,
            stdout.as_raw_fd(),
            None,
            CHUNK_SIZE,
            SpliceFFlags::SPLICE_F_MOVE | SpliceFFlags::SPLICE_F_MORE,
        )?;
        if bytes == 0 {
            // No more data to read, return successfully after reporting one
            // last time
            report(bytes_so_far, start_time.elapsed());
            return Ok(());
        }

        // Update what we have transferred so far
        bytes_so_far += bytes;

        // Report if it is time to do so
        let now = Instant::now();
        if now >= next_report_time {
            next_report_time = now + Duration::from_secs_f64(REPORT_PERIOD);
            report(bytes_so_far, start_time.elapsed());
        }
    }
}

fn report(byte_count: usize, elapsed: Duration) {
    // Use the byte_unit crate to do all the unit conversions and display logic
    use byte_unit::{Byte, ByteUnit};

    let adjusted_byte_count = Byte::from_unit(byte_count as f64, ByteUnit::B)
        .unwrap()
        .get_appropriate_unit(true);

    // Get the average throughput since the start
    let throughput = byte_count as f64 / elapsed.as_secs_f64();
    let adjusted_throughput = Byte::from_unit(throughput, ByteUnit::B)
        .unwrap()
        .get_appropriate_unit(true);

    // Print report to standard error
    // We use some padding to make the number of characters outputted stable so
    // that the carriage return trick properly overwrites all previous output
    eprint!(
        "{:>10} | {:>10}/s | {:>10}\r",
        adjusted_byte_count.to_string(),
        adjusted_throughput.to_string(),
        // Debug for Duration doesn't pad properly, so format beforehand
        format!("{:.1?}", elapsed)
    );
}
```

A more full-fledged clone of `pv` in Rust would have nicer error messages, for
example when standard input is the terminal rather than a pipe. It could possibly
also support file inputs. Hide the cursor in the terminal while updating the
report. And have a colourful output! 🤩

I hope you learned something just like I did while writing this, see you around!
