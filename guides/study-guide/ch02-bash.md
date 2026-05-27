## Bash and Shell Scripting

Shell fluency is a daily multiplier on this job. Python orchestrates tests; Bash is how you interrogate hardware the moment it misbehaves — "the board failed" becomes "endpoint `0000:03:00.0` trained Gen3 x8, BadTLP Advanced Error Reporting (AER) counter climbing, here is the sysfs value" in the time it takes to type one pipeline. This chapter is a working reference organized around the problems you actually hit on the station.

### When to Reach for Bash vs. Python

The boundary matters. Choosing wrong costs you either time (bash where you need Python) or maintainability (Python where bash would have been five lines).

| Reach for Bash when | Reach for Python when |
|---|---|
| Interactive hardware query at a prompt | Structured logic — state machines, retries with backoff |
| Piping Command-Line Interface (CLI) tools (`lspci`, `nvme`, `ethtool`, `dmesg`) | Instrument comms (serial/SCPI/USB/TCP) |
| System-admin one-offs under ~100 lines | Parsing structured data robustly (JSON, CSV, XML) |
| Cron/systemd wrappers, Continuous Integration (CI) scaffolding | Anything a teammate will maintain |
| "Do I see 4 GPUs right now?" | "Assert exactly 4 GPUs every run, log to DB, fail the test" |
| Environment setup, deployment glue | pytest fixtures, PySide6 GUIs |

The one-sentence rule: **Bash glues programs together and pokes at the system; Python owns logic, state, and anything maintained.** The moment you find yourself building data structures or needing real error handling in Bash, stop and rewrite in Python.

In practice the two interoperate constantly. Your ATF is Python; it shells out for the parts where no clean library exists:

```bash
# Bash one-liner at the prompt -- fastest path to "do I have 4 GPUs?":
lspci | grep -i nvidia | wc -l
```

```python
# Same check, hardened, inside a pytest fixture:
import subprocess
result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
gpu_count = result.stdout.count("NVIDIA")
assert gpu_count == 4, f"Expected 4 GPUs, found {gpu_count}"
```

And Bash scripts call back into Python for the parts Bash handles badly:

```bash
# Bash orchestrates the station; Python does structured analysis:
rate=$(python3 /opt/atf/analyze.py --input "$RESULTS_CSV" --metric pass_rate)
[[ "${rate%.*}" -ge 95 ]] || die "Yield below threshold: ${rate}%"
```

Reading sysfs attributes directly from Python (`pathlib.Path.read_text()`) is usually *better* than shelling out to parse `lspci` text — zero subprocess overhead, no parsing fragility. On the bench, interactively, Bash wins for speed of thought.

---

## Shell Mechanics

### Variables and Quoting

```bash
# Assignment: NO SPACES around =. "X = 1" tries to RUN a command named X.
DEVICE="/dev/nvme0n1"
BDF="0000:03:00.0"
LOG="/var/log/mfg_test/$(date +%Y%m%d_%H%M%S).log"
readonly MAX_RETRIES=5            # readonly: any later reassignment is an error
declare -i counter=0             # integer type: arithmetic happens automatically
export PATH="/opt/atf/bin:$PATH" # export: child processes inherit this variable

# Reference with ${}: braces are optional but eliminate ambiguity.
echo "$DEVICE"
echo "${BDF}_suffix"   # WITHOUT braces: $BDF_suffix would look up the var named BDF_suffix
```

Quoting is the single largest source of Bash bugs:

```bash
echo "$DEVICE"           # double quotes: variables EXPAND       -> /dev/nvme0n1
echo '$DEVICE'           # single quotes: LITERAL, no expansion  -> $DEVICE
echo "$BDF and $(date)"  # double quotes still allow $(...) substitution
echo 'cost is $5'        # single quotes protect literal dollar signs

# ALWAYS double-quote variable expansions. Unquoted = word-split + glob-expand:
path="/mnt/my board/file"
rm $path                 # runs: rm /mnt/my board/file  (THREE args; disaster)
rm "$path"               # runs: rm "/mnt/my board/file" (one arg; correct)
```

### Command Substitution and Arithmetic

```bash
# Prefer $(...) over backticks: nestable, readable, unambiguous.
SPEED=$(cat /sys/bus/pci/devices/$BDF/current_link_speed)
GPU_COUNT=$(lspci | grep -c 'NVIDIA')
NOW=$(date +%s)            # epoch seconds

# Integer arithmetic with (( )) or $(( )). Bash has NO floating point.
count=$((count + 1))
pct=$(( passed * 100 / total ))   # integer division: 7/2 == 3, not 3.5
(( failed > 0 )) && echo "some failed"   # (( )) as a test: nonzero == true

# Floating point: pipe to bc or use awk BEGIN.
pct=$(echo "scale=2; $passed * 100 / $total" | bc)
margin=$(awk -v a="$volts" -v b="$nominal" 'BEGIN{printf "%.3f", a-b}')
```

### Parameter Expansion (Built-in String Surgery)

This replaces a surprising amount of `sed`/`awk`. These show up constantly when massaging BDFs, paths, and log lines:

```bash
echo "${BDF:-default}"   # value of BDF, or "default" if unset/empty (no assignment)
echo "${BDF:=default}"   # same, but ALSO assigns "default" to BDF
echo "${BDF:?must set}"  # error and exit with "must set" if unset/empty (guard at top of script)
echo "${BDF:+yes}"       # "yes" if BDF is set/non-empty, else empty
echo "${#BDF}"           # string LENGTH
echo "${BDF^^}"          # UPPERCASE (bash 4+)
echo "${BDF,,}"          # lowercase (bash 4+)

# Substring removal. # strips a PREFIX, % strips a SUFFIX. Double the char for "longest match".
filepath="/var/log/test/results.csv"
echo "${filepath##*/}"   # results.csv     (## = longest leading */   -> basename equivalent)
echo "${filepath#*/}"    # var/log/test/results.csv  (# = shortest leading match)
echo "${filepath%/*}"    # /var/log/test   (% = shortest trailing /*  -> dirname equivalent)
echo "${filepath%.csv}"  # /var/log/test/results  (strip known extension)
echo "${filepath##*.}"   # csv             (just the extension)

# In-place substitution (no sed needed for simple cases):
echo "${filepath/test/prod}"   # /var/log/prod/results.csv  (first match)
echo "${filepath//o/0}"        # /var/l0g/test/results.csv  (all matches: //)

# Substrings by offset:length:
s="0000:03:00.0"
echo "${s:5:2}"          # 03  (2 chars from index 5 -- the bus number)
echo "${s:0:4}"          # 0000 (the PCI domain)
```

### Exit Codes

Every command returns an integer status. **0 = success, non-zero = failure.** This is the bedrock of pass/fail logic.

```bash
lspci -s "$BDF" &>/dev/null   # discard all output; check only the exit status
echo $?                       # 0 = found, 1 = not found

# Use exit codes directly in conditionals -- no need to compare $? explicitly:
if lspci -s "$BDF" &>/dev/null; then echo "present"; else echo "absent"; fi

# Chain on success (&&) or failure (||):
mount /mnt/usb && cp results.csv /mnt/usb/ || echo "backup failed"

# Conventional codes:
# 0          success
# 1-125      application-defined failure
# 126        command exists but is not executable
# 127        command not found
# 128+N      killed by signal N  (130 = Ctrl+C/SIGINT, 137 = SIGKILL, 143 = SIGTERM)
exit 0    # PASS
exit 1    # generic FAIL
```

### Redirection and File Descriptors

Three standard file descriptors: **0 = stdin, 1 = stdout, 2 = stderr.** Redirection rewires where they point. This matters enormously for logging test output cleanly.

```bash
command > file           # stdout -> file (TRUNCATE / overwrite)
command >> file          # stdout -> file (APPEND)
command 2> errlog        # fd 2 (stderr) -> errlog
command 2>&1             # redirect stderr to wherever stdout currently points
command > out 2>&1       # ORDER MATTERS: stdout -> out first, THEN stderr -> same place
command &> file          # bash shorthand: both stdout and stderr -> file
command < input.txt      # stdin FROM file
command 2>/dev/null      # discard only errors (when a sysfs file may not exist)
command &>/dev/null      # discard everything (just give me the exit code)

# The ordering trap:
do_test > test.log 2>&1   # CORRECT: both streams land in test.log
do_test 2>&1 > test.log   # WRONG: stderr goes to the original stdout (terminal),
                          # then stdout goes to the file. stderr never reaches the file.
```

### Here-Documents and Here-Strings

```bash
# Here-doc: feed a multi-line block as stdin.
# QUOTE the delimiter to DISABLE variable expansion (everything is literal):
cat << 'EOF' > /etc/test/config.ini
[psu]
voltage = 48.0
current_limit = 20.0
EOF

# Unquoted delimiter: variables ARE expanded:
cat << EOF
Testing board $SERIAL at $(date)
Expected speed: $EXPECTED
EOF

# Here-string (<<<): a single string as stdin. Handy with grep/read/bc:
grep -q "16 GT/s" <<< "$SPEED" && echo "Gen4"
read -r bus dev func <<< "${BDF//[:.]/ }"   # split BDF "0000:03:00.0" into three fields
```

### Pipes and tee

A pipe (`|`) connects stdout of one command to stdin of the next. Process substitution (`< <(...)`) lets you feed command output as a file to a redirect, which keeps the receiving shell in the current process rather than a subshell.

```bash
lspci | grep -i nvidia | wc -l            # count NVIDIA devices
dmesg | tail -50 | grep -i error          # recent kernel errors

# tee: write to a file AND pass the stream onward (log without losing data):
run_test.sh | tee test.log | grep -i fail  # full output to file, failures to terminal
echo "started $(date)" | tee -a runs.log   # -a appends instead of truncating

# Process substitution: feed command output as a "file" via a named pipe:
diff <(lspci -t) <(cat expected_topology.txt)   # compare live topology vs saved baseline
while IFS= read -r line; do
    process "$line"
done < <(lspci -d 10de:)   # loop body runs in THIS shell, not a subshell -- counters survive
```

The subshell trap is one of the most common Bash bugs in test scripts:

```bash
count=0
lspci -d 10de: | while read -r _; do (( count++ )); done
echo "$count"   # prints 0 -- the pipe created a subshell; count was discarded!

# Fix: use process substitution so the while loop runs in the current shell:
while read -r _; do (( count++ )); done < <(lspci -d 10de:)
echo "$count"   # correct
```

---

## Conditionals and Tests

Use `[[ ... ]]` (a Bash keyword) rather than `[ ... ]` (POSIX `test`). `[[ ]]` does not word-split or glob-expand its operands, supports `&&`/`||`/`=~`, and avoids a class of subtle bugs from unquoted variables.

### String, Numeric, and File Tests

```bash
# String tests:
[[ "$status" == "PASS" ]]              # equality (== or =)
[[ "$status" != "FAIL" ]]              # inequality
[[ -z "$var" ]]                        # true if empty or unset (zero length)
[[ -n "$var" ]]                        # true if non-empty
[[ "$str" == *.log ]]                  # GLOB match (right side is a pattern: * ? [ ])
[[ "$bdf" =~ ^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]$ ]]   # =~ REGEX (ERE)
echo "${BASH_REMATCH[0]}"              # after =~, captured groups land in BASH_REMATCH[]
# DO NOT quote the right side of =~ if it contains regex metacharacters --
# quoting forces a literal string comparison instead.

# Numeric tests (use -eq family inside [[ ]] -- == compares as strings):
[[ "$count" -eq 4 ]]    # equal
[[ "$count" -ne 0 ]]    # not equal
[[ "$count" -gt 10 ]]   # greater than
[[ "$count" -lt 100 ]]  # less than
[[ "$count" -ge 4 ]]    # >=
[[ "$count" -le 4 ]]    # <=
# Or use (( )) arithmetic context, which reads more naturally:
(( count == 4 ))
(( count > 10 && count < 100 ))
(( temp >= 70 )) && echo "OVERHEAT"
# Gotcha: [[ "08" -eq 8 ]] is TRUE (numeric); [[ "08" == 8 ]] is FALSE (string "08" != "8").

# File tests:
[[ -e "$path" ]]     # exists (file, dir, symlink, device)
[[ -f "$file" ]]     # exists AND is a regular file
[[ -d "$dir" ]]      # exists AND is a directory
[[ -L "$path" ]]     # is a symbolic link
[[ -b "$dev" ]]      # is a block device  (e.g. /dev/nvme0n1)
[[ -c "$dev" ]]      # is a character device (e.g. /dev/ttyUSB0)
[[ -r "$file" ]]     # readable
[[ -w "$file" ]]     # writable
[[ -x "$file" ]]     # executable
[[ -s "$file" ]]     # exists AND has size > 0 (non-empty)
[[ "$f1" -nt "$f2" ]]  # f1 is NEWER than f2 (modification time)

# Manufacturing guard:
[[ -c /dev/ttyUSB0 ]] || die "Programmer cable not detected on /dev/ttyUSB0"
```

### Branching

```bash
# Combining conditions:
[[ "$speed" == "16 GT/s" && "$width" == "16" ]]   # AND (both must be true)
[[ "$speed" != "16 GT/s" || "$width" != "16" ]]   # OR (either is true)
[[ ! -f "$lockfile" ]]                             # NOT

if [[ "$status" == "PASS" ]]; then
    log "OK"
elif [[ "$status" == "FAIL" ]]; then
    log "FAILED" "ERROR"
else
    log "UNKNOWN status: $status" "WARN"
fi

# case (switch) -- cleaner than a long if/elif chain; patterns are GLOBS:
case "$device_type" in
    gpu)           test_gpu  "$bdf" ;;
    nvme|ssd)      test_nvme "$bdf" ;;    # multiple patterns with |
    nic|eth*)      test_nic  "$bdf" ;;    # glob: anything starting with "eth"
    *)             die "Unknown device type: $device_type" ;;   # default/fallthrough
esac
```

---

## Loops

```bash
# for over a word list (command substitution, glob, or array):
for dev in $(lspci -d 10de: -D | awk '{print $1}'); do   # -D shows full BDF with domain
    speed=$(cat "/sys/bus/pci/devices/$dev/current_link_speed" 2>/dev/null)
    echo "$dev: $speed"
done

# C-style for (counter loops):
for ((i = 0; i < 16; i++)); do
    echo "Lane $i"
done

# for over files via a glob -- always guard against an empty match:
for file in /var/log/mfg_test/*.log; do
    [[ -f "$file" ]] || continue   # if glob matched nothing it stays literal; skip it
    process "$file"
done

# while with an arithmetic condition (retry loop with backoff):
retries=0
while (( retries < MAX_RETRIES )); do
    try_connect && break
    (( retries++ ))
    sleep 1
done
(( retries < MAX_RETRIES )) || die "Failed to connect after $MAX_RETRIES tries"

# until (runs WHILE the condition is FALSE) -- wait for DUT to come up:
until ping -c1 -W1 192.168.100.10 &>/dev/null; do
    echo "Waiting for device to boot..."
    sleep 2
done
```

### Reading Input Line by Line (the Right Way)

```bash
# while IFS= read -r line  is the correct idiom.
# IFS=   stops leading/trailing whitespace from being trimmed.
# -r     stops backslashes from being interpreted as escape sequences.

while IFS= read -r line; do
    echo "Processing: $line"
done < /opt/test/serials.txt

# Read a COMMAND's output line by line. Use process substitution < <(...)
# so the loop body runs in the CURRENT shell (variables set inside survive after the loop):
while IFS= read -r line; do
    bdf=$(awk '{print $1}' <<< "$line")
    echo "Found device: $bdf"
done < <(lspci -d 10de:)
```

---

## The Text-Processing Toolkit

`grep` filters, `sed` edits streams, `awk` handles fields/aggregation, `cut`/`sort`/`uniq`/`tr` do quick column and frequency work, `find`/`xargs` act on files. Master these and you can extract any value from `lspci`, `nvme`, `ethtool`, `dmesg`, or a results CSV.

### grep

```bash
grep 'error' /var/log/syslog        # case-sensitive (BRE by default)
grep -i 'error' log.txt             # case-INsensitive
grep -c 'FAIL' results.txt          # COUNT matching lines (not print them)
grep -n 'TODO' main.py              # prefix each hit with its line NUMBER
grep -rn 'pyvisa' src/              # -r recursive, -n line numbers (search a whole tree)
grep -v 'DEBUG' app.log             # INVERT: print lines that do NOT match
grep -l 'error' *.log               # list only FILENAMES that contain a match
grep -L 'PASS' *.log                # filenames that do NOT contain a match (find failures)
grep -w 'fail' log.txt              # whole WORD only (won't match "failure" or "default")
grep -A3 -B2 'error' log.txt        # context: 3 lines After, 2 Before each match
grep -C2 'error' log.txt            # -C: context both sides (2 before AND 2 after)
grep -o 'Gen[0-9]' log.txt          # -o: print ONLY the matched text, not the whole line
grep -q 'PASS' log.txt              # -q: QUIET -- no output, just the exit code (use in if tests)
grep -m1 'error' big.log            # stop after the FIRST match (fast on huge logs)

# Regex flavors:
grep -E 'error|warn|critical' log   # -E: Extended Regex (ERE): | + ? () without backslashes
grep -P '\d{4}-\d{2}-\d{2}' log     # -P: Perl-Compatible Regex: \d \w \s lookarounds

# The killer PCRE trick: -oP with \K to extract a value after an anchor.
# \K discards everything matched before it, so the anchor is required but not printed.
grep -oP 'Speed \K[^,]+' lspci.txt  # -> "16GT/s" from "LnkSta: Speed 16GT/s, Width x16"
grep -oP 'Width \Kx[0-9]+' lspci.txt  # -> "x16"
grep --color=always 'error' f | less -R   # keep color highlights when paging

# Pull link speed and width for one device:
lspci -s 03:00.0 -vvv | grep -oP 'LnkSta:.*Speed \K[^,]+'    # -> 16GT/s
lspci -s 03:00.0 -vvv | grep -oP 'LnkSta:.*Width \Kx[0-9]+' # -> x16
```

### sed

`sed` edits text as it streams past. Substitution (`s///`) is the core; it also prints line ranges, deletes lines, and inserts text.

```bash
sed 's/old/new/' file.txt            # substitute FIRST occurrence per line
sed 's/old/new/g' file.txt           # g: GLOBAL -- every occurrence on each line
sed 's/old/new/gi' file.txt          # gi: global + case-insensitive
sed -i 's/old/new/g' file.txt        # -i: IN-PLACE edit (edits the file; no stdout)
sed -i.bak 's/old/new/g' file.txt    # -i.bak: in-place, save original as file.txt.bak

sed -n '10,20p' file.txt             # -n suppresses default print; p prints lines 10-20
sed -n '/START/,/END/p' file.txt     # print everything between two patterns (inclusive)
sed -n '$p' file.txt                 # print only the last line ($ = last line in sed)
sed '/^$/d'  file.txt                # DELETE blank lines
sed '/^#/d'  file.txt                # delete comment lines (starting with #)
sed '/^\s*#/d; /^\s*$/d' file.txt    # strip comments AND blank lines (two commands)
sed 's/[[:space:]]*$//' file.txt     # strip TRAILING whitespace
sed '1i\HEADER' file.txt             # INSERT "HEADER" before line 1
sed '$a\FOOTER' file.txt             # APPEND "FOOTER" after the last line
sed '3d' file.txt                    # delete line 3
sed -n '2~3p' file.txt               # print every 3rd line starting at line 2 (GNU step)

# Capture groups with -E (ERE). \1 \2 ... refer to parenthesized groups.
# You can use any delimiter after s: s#...#...# avoids escaping / in paths.
sed -E 's/([0-9]+)GT\/s/Speed=\1/g' lspci.txt   # "16GT/s" -> "Speed=16"
sed -E 's#^(0000:[0-9a-f:.]+).*#\1#' devs.txt   # keep only the BDF

echo "0000:03:00.0" | sed 's/[:.]/ /g'   # "0000 03 00 0" -- split BDF into fields
```

### awk

`awk` reads line by line, splits each line into fields (`$1`, `$2`, ...; `$0` = whole line; `NF` = field count; `NR` = record/line number), and runs `pattern { action }` rules. It is the right tool for columnar data and aggregation.

```bash
awk '{print $1}' file.txt             # first whitespace-delimited field of every line
awk '{print $NF}' file.txt            # the LAST field ($NF = field at position NF)
awk '{print $(NF-1)}' file.txt        # second-to-last field
awk -F: '{print $1, $3}' /etc/passwd  # -F: set the field separator (here ":") -> username, uid
awk -F, '{print $1, $4}' results.csv  # CSV with comma separator
awk -F'\t' '{print $2}' data.tsv      # tab-separated

awk 'NR > 2' nvme_list.txt            # skip the first 2 header lines -- print the rest
awk 'NR >= 10 && NR <= 20' file.txt   # line range (equivalent to sed -n '10,20p')
awk 'NR % 2 == 0' file.txt            # even-numbered lines only

awk '$3 > 100' data.txt               # print lines where field 3 (numeric) exceeds 100
awk '$4 == "FAIL"' results.csv        # lines where field 4 equals the string FAIL
awk -F, '$4=="FAIL" {print $1, $2}'   # combine condition with custom print action
awk '/error/ {print NR": "$0}'        # for lines matching /error/, print "lineno: full line"
awk '!/DEBUG/' app.log                # print lines that do NOT match (negate with !)

# Aggregation -- awk shines here. BEGIN runs before input, END runs after.
awk '{sum += $3} END {print "Total:", sum}' data.txt
awk '{sum += $1; n++} END {printf "avg=%.2f\n", sum/n}' nums.txt
awk -F, 'NR>1 {c[$4]++} END {for (k in c) print k, c[k]}' r.csv  # frequency by category
awk '{gsub(/old/, "new"); print}' file  # global substitution then print

# ALWAYS pass shell variables in with -v (never interpolate "$x" inside the awk program):
threshold=70
awk -v t="$threshold" '$2 > t {print $1, "OVERHEAT", $2}' temps.txt
```

Multi-line awk to walk `lspci -vvv` output and emit a clean Bus/Device/Function (BDF)/Speed/Width table:

```bash
lspci -vvv 2>/dev/null | awk '
    BEGIN { OFS = "\t"; print "BDF", "Speed", "Width" }
    /^[0-9a-f]/ { bdf = $1 }                  # non-indented hex line = new device
    /LnkSta:/ && !/LnkSta2:/ {                # link-status line (skip secondary LnkSta2)
        gsub(/,/, "")                         # drop commas so fields are clean
        for (i = 1; i <= NF; i++) {
            if ($i == "Speed") speed = $(i+1)
            if ($i == "Width") width = $(i+1)
        }
        print bdf, speed, width
    }
'
```

### cut, sort, uniq, tr

```bash
# cut: extract columns by delimiter or character position
cut -d: -f1 /etc/passwd              # field 1, ":" delimited (all usernames)
cut -d, -f2,4 data.csv               # fields 2 AND 4
cut -d, -f2-5 data.csv               # fields 2 through 5
cut -c1-10 file.txt                  # characters 1-10 of each line

# sort
sort file.txt                        # ascending, lexicographic
sort -n nums.txt                     # NUMERIC sort (9 < 10; not the case lexicographically)
sort -h sizes.txt                    # human-numeric: understands 2K, 5M, 1G
sort -r file.txt                     # REVERSE (descending)
sort -u file.txt                     # sort and de-duplicate in one step
sort -t, -k3 -rn data.csv            # CSV: field 3, reverse numeric
sort -k2,2 -k1,1n file               # multi-key: by field 2, then numerically by field 1

# uniq -- ONLY collapses ADJACENT duplicates; always sort first.
sort file | uniq                     # remove duplicate lines
sort file | uniq -c                  # prefix each unique line with its count
sort file | uniq -c | sort -rn       # the classic FREQUENCY-COUNT pipeline
sort file | uniq -d                  # show ONLY lines that appeared more than once
sort file | uniq -u                  # show ONLY lines that appeared exactly once

# tr -- translate or delete characters (operates on stdin only)
tr 'a-z' 'A-Z' < file                # uppercase
tr -d '\r' < dos.txt > unix.txt      # strip carriage returns (DOS->Unix line endings)
tr -s ' ' < file                     # SQUEEZE runs of spaces into a single space
tr -s ' \t' '\n' < file              # turn runs of space/tab into newlines (one token/line)
tr -cd '[:print:]\n' < f             # keep only printable chars + newlines (strip binary noise)
```

Top-10 most common error messages in a test log:

```bash
grep '\[ERROR\]' test.log \
  | sed 's/.*\[ERROR\] //' \         # strip prefix up to and including [ERROR]
  | sort \                           # group identical messages (REQUIRED before uniq)
  | uniq -c \                        # count consecutive duplicates
  | sort -rn \                       # rank by count, highest first
  | head -10                         # top 10
```

### find and xargs

```bash
find . -name '*.py' -type f                    # all regular .py files
find . -iname '*.LOG' -type f                  # case-insensitive name match
find /var/log -name '*.log' -mtime +30         # modified MORE than 30 days ago
find /var/log -name '*.log' -mtime -1          # modified within the LAST 1 day
find . -type f -size +100M                     # files larger than 100 MB
find . -type f -newer reference_file           # modified after a reference file
find . -type d -empty                          # empty directories
find . -maxdepth 1 -name '*.csv'               # only the current directory (no recursion)

# Acting on results:
find /var/log -name '*.log' -mtime +30 -delete            # delete old logs (find's own -delete)
find . -name '*.py' -exec wc -l {} +                       # {} + : one invocation for all files
find . -name '*.tmp' -exec rm {} \;                        # {} \; : one invocation PER FILE

# xargs: turn stdin into arguments for a command.
# The SAFE pairing for filenames with spaces: -print0 on find + -0 on xargs.
find . -name '*.pyc' -print0 | xargs -0 rm
find . -name '*.py' -print0 | xargs -0 grep -l 'import pyvisa'
pgrep -f pytest | xargs -r kill -15    # -r: skip the kill if there are no PIDs
```

### tee, diff, comm, paste, column

```bash
# tee: split a stream to a file AND onward (logging without losing the stream)
lspci -vvv | tee lspci.txt | grep NVIDIA

# diff: line-by-line comparison
diff -u fileA fileB                               # unified (git-style) diff
diff <(lspci -t) <(cat expected_topology.txt)     # compare live command vs saved baseline

# comm: compare two SORTED files. Three columns: only-in-1, only-in-2, in-both.
# The NUMBER you pass = the column you SUPPRESS.
comm -23 <(sort a) <(sort b)   # lines ONLY in a (suppress col 2 and 3)
comm -12 <(sort a) <(sort b)   # lines in BOTH (suppress col 1 and 2)
comm -13 <(sort a) <(sort b)   # lines ONLY in b

# paste: join lines side by side
paste -d, file1 file2          # merge two files into comma-separated columns

# column: align output into a readable table
mount | column -t
printf 'BDF Speed Width\n0000:03:00.0 16GT/s x16\n' | column -t
```

---

## Robust Scripting

### The Header Every Production Script Starts With

```bash
#!/bin/bash
set -euo pipefail
IFS=$'\n\t'
```

`set -euo pipefail` is the single most important line in any production script. Each flag prevents a different class of silent, dangerous failure:

```bash
# -e  (errexit):   EXIT IMMEDIATELY if any command returns non-zero. Without it, a script
#                  sails on after a failed step -- catastrophic when you're controlling a PSU
#                  or actuator and step 3 failed but step 4 ("ramp voltage") still runs.
#
# -u  (nounset):   Treat an UNSET variable as an error. Catches typos:
#                  rm -rf "/data/$DEVCE/"  -- with typo'd $DEVCE expanding to "",
#                  that becomes rm -rf "/data//" . With -u it errors out instead.
#
# -o pipefail:     A pipeline's exit status is the FIRST non-zero command, not just the last.
#                  Without it: broken_tool | grep PASS  reports SUCCESS (grep ran fine)
#                  even though broken_tool crashed. Your test would falsely "pass."
#
# IFS=$'\n\t':     Internal Field Separator = newline + tab only (drop the default space).
#                  Stops filenames and values WITH SPACES from splitting into multiple words
#                  in unquoted expansions and for-loops.
```

Caveats so `set -e` does not surprise you:

```bash
# A command whose failure is EXPECTED must be made explicit, or it kills the script:
grep PASS log.txt || true                 # "|| true" intentionally swallows the failure
count=$(grep -c PASS log.txt) || count=0  # handle the no-match exit code explicitly

# Commands tested by if/while/&&/|| do NOT trigger -e exit -- they are the test.
if grep -q FAIL log.txt; then ...; fi   # grep returning non-zero here is fine; it's the condition
```

### Logging, Fatal-Exit, and Self-Locating Helpers

```bash
# Resolve the script's own directory regardless of where it is called from:
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LOG="/var/log/mfg_test/$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$(dirname "$LOG")"

log() {                                    # timestamped, leveled, tees to console + file
    local level="${2:-INFO}"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')][$level] $1" | tee -a "$LOG"
}
die() {                                    # log a fatal message and exit non-zero
    log "$1" "FATAL"
    exit 1
}

# Input validation up front -- fail fast with a clear usage message:
[[ $# -ge 1 ]] || die "Usage: $0 <bdf> [expected_speed]"
BDF="$1"
EXPECTED="${2:-16 GT/s}"
```

### Functions, Locals, and Return Codes

```bash
check_link() {
    local bdf="$1"            # ALWAYS declare function variables 'local'
    local expected="$2"       # avoids clobbering globals with the same name
    local actual
    actual=$(cat "/sys/bus/pci/devices/$bdf/current_link_speed" 2>/dev/null) || {
        log "Cannot read link speed for $bdf" "ERROR"
        return 1              # functions return an EXIT STATUS (0-255), not a value
    }
    [[ "$actual" == "$expected" ]]   # function's return status = result of this test
}

# Call it and branch on the status:
if check_link "$BDF" "$EXPECTED"; then
    log "PASS: $BDF at $EXPECTED"
else
    log "FAIL: $BDF not at $EXPECTED"
fi

# To return a VALUE (string/number), echo it and capture with $(...):
get_link_speed() { cat "/sys/bus/pci/devices/$1/current_link_speed" 2>/dev/null; }
speed=$(get_link_speed "$BDF")
```

### Flag Parsing with getopts

```bash
usage() { echo "Usage: $0 -s SERIAL [-v] [-c CONFIG]"; exit 1; }

VERBOSE=0; CONFIG="/opt/test/default.json"
while getopts "s:c:v" opt; do
    case "$opt" in
        s) SERIAL="$OPTARG" ;;    # -s takes an argument (note the colon after s in the string)
        c) CONFIG="$OPTARG" ;;    # -c takes an argument
        v) VERBOSE=1 ;;           # -v is a boolean flag
        *) usage ;;
    esac
done
shift $((OPTIND - 1))   # remove parsed flags; $@ now contains positional arguments
[[ -n "${SERIAL:-}" ]] || die "-s SERIAL is required"
```

### Arrays

```bash
# Indexed arrays:
devices=("gpu0" "gpu1" "gpu2" "gpu3")
echo "${devices[0]}"          # gpu0
echo "${devices[@]}"          # all elements (always use quotes: "${devices[@]}")
echo "${#devices[@]}"         # element count
echo "${!devices[@]}"         # the indices (0 1 2 3)
devices+=("gpu4")             # append

# Capture command output into an array, one element per line:
mapfile -t bdfs < <(lspci -d 10de: -D | awk '{print $1}')   # -t strips the newlines
echo "Found ${#bdfs[@]} GPUs"

# Iterate safely (quoted @ preserves elements with spaces intact):
for d in "${devices[@]}"; do echo "$d"; done

# Associative arrays (bash 4+) -- key/value pairs, ideal for a results table:
declare -A results
results["0000:01:00.0"]="PASS"
results["0000:02:00.0"]="FAIL"
for bdf in "${!results[@]}"; do
    log "$bdf -> ${results[$bdf]}"
done
```

---

## Signals and Traps (Hardware Safety)

When a script controls real hardware -- a PSU, actuators, a pressure rig, heaters -- signal handling is a **safety requirement, not a nicety.** If an operator hits Ctrl+C during a stress run and the script dies without zeroing the supply, you can damage a board.

A `trap` registers a handler that runs when the script exits or receives a signal.

```bash
#!/bin/bash
set -euo pipefail

cleanup() {
    trap '' SIGINT SIGTERM         # ignore further signals -- prevent re-entry
    log "Restoring safe state..."
    set_voltage 0 || true          # zero the supply; || true so one failure won't abort cleanup
    disable_psu    || true
    release_actuators || true
    log "Safe state reached."
}

# EXIT catches everything: normal exit, set -e error exit, and AFTER signal handlers run.
# Without trapping EXIT, a normal successful exit would skip cleanup.
trap cleanup EXIT
trap cleanup SIGINT                # Ctrl+C
trap cleanup SIGTERM               # kill / systemctl stop
trap cleanup SIGHUP                # controlling terminal closed (SSH dropped)

set_voltage 48
run_stress_test
log "Test complete."
# cleanup() runs automatically here via the EXIT trap.
```

Critical distinctions:

```bash
# EXIT is the catch-all. Trap this first.
# Per-signal handlers with conventional exit codes (128+N):
trap 'log "Interrupted"; cleanup; exit 130' SIGINT    # 128 + 2
trap 'log "Terminated";  cleanup; exit 143' SIGTERM   # 128 + 15

# SIGKILL (kill -9) and SIGSTOP CANNOT be trapped or ignored.
# NEVER send kill -9 to a hardware-control script if you can avoid it.
# Send SIGTERM first; give the trap time to run; -9 is the last resort.

# List all signal names and numbers:
kill -l
```

---

## Logging and Log Rotation

### Structured Logging Pattern

```bash
# Timestamped log file with a die() that flushes the message before exiting:
readonly LOG_DIR="/var/log/mfg_test"
readonly LOG="${LOG_DIR}/test_$(date +%Y%m%d_%H%M%S)_${SERIAL:-unknown}.log"
mkdir -p "$LOG_DIR"

log() {
    local ts level msg
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    level="${2:-INFO}"
    msg="[$ts][$level] $1"
    echo "$msg" | tee -a "$LOG"
    [[ "$level" == "ERROR" || "$level" == "FATAL" ]] && echo "$msg" >&2   # also to stderr
}

die() { log "$1" "FATAL"; exit 1; }

# Capture a command's output AND status while preserving both streams:
run_and_log() {
    local cmd_output exit_code
    cmd_output=$("$@" 2>&1)
    exit_code=$?
    log "$cmd_output"
    return $exit_code
}
```

### Log Rotation

Test stations accumulate logs. Two approaches:

```bash
# 1. logrotate (standard system tool; config in /etc/logrotate.d/):
# Create /etc/logrotate.d/mfg-test:
#
# /var/log/mfg_test/*.log {
#     daily
#     rotate 30           # keep 30 rotated copies
#     compress            # gzip old logs
#     delaycompress       # don't compress the most recent rotated log (still readable if open)
#     missingok           # don't error if no log file exists
#     notifempty          # don't rotate an empty file
#     create 640 testeng testeng  # permissions and ownership for new log file
# }

# 2. Find + compress inline (for ad-hoc cleanup from a script or cron):
find /var/log/mfg_test -name '*.log' -mtime +30 -print0 | xargs -0 gzip
find /var/log/mfg_test -name '*.gz'  -mtime +90 -delete   # then delete very old compressed logs

# 3. A simple self-managing log directory (cap at N most recent files):
clean_old_logs() {
    local dir="$1" keep="${2:-50}"
    ls -1t "${dir}"/*.log 2>/dev/null | tail -n "+$((keep+1))" | xargs -r rm
}
```

---

## Scheduling: cron and systemd Timers

### cron

```bash
crontab -e          # edit YOUR user's crontab (opens $EDITOR)
crontab -l          # list current entries
crontab -r          # REMOVE ALL entries (no confirmation -- be careful)
sudo crontab -e -u testeng   # edit another user's crontab

# Five time fields, then the command:
#   minute (0-59)  hour (0-23)  day-of-month (1-31)  month (1-12)  day-of-week (0-7, 0/7=Sun)
#   *  = every    */N = every Nth    a-b = range    a,b,c = list

0  *    * * *   /opt/test/pcie_verify.sh >> /var/log/pcie.log 2>&1   # top of every hour
0  */6  * * *   /opt/test/nvme_health.sh >> /var/log/nvme.log 2>&1   # every 6 hours
30 7    * * 1-5 /opt/test/station_self_check.sh                       # weekdays 07:30
*/5 *   * * *   /opt/test/check_all_stations.sh                       # every 5 minutes
0  6    * * *   /opt/test/yield_report.sh | mail -s "Yield" team@zoox.com  # daily email
@reboot         /opt/test/startup_init.sh >> /var/log/startup.log 2>&1     # once at boot
```

Cron gotchas -- memorize these, they bite everyone at least once:

```bash
# 1. PATH is MINIMAL. Always use absolute paths: /usr/bin/python3, not python3.
#
# 2. NO interactive-shell environment. Set variables in the crontab directly:
#       PYTHONPATH=/opt/atf
#       SHELL=/bin/bash
#       0 * * * * /usr/bin/python3 /opt/test/check.py
#
# 3. The working directory is $HOME. Use absolute paths for everything.
#
# 4. Output goes NOWHERE unless redirected. Always end with: >> logfile 2>&1
#
# 5. cron runs with /bin/sh. For bash-specific syntax, use a #!/bin/bash script.
#
# 6. % is special in crontab (means newline). Escape it as \% or put it in a script.

# Debugging cron:
systemctl status cron           # is the daemon running?  (or 'crond' on RHEL)
grep CRON /var/log/syslog       # did your job fire?
# Golden rule: run the command manually first with the cron-minimal environment, then schedule.
```

### systemd Timers

Timers over cron: logs land in `journalctl`, you get dependency ordering, `RandomizedDelaySec` prevents 50 stations all hammering the dashboard at `:00`, and `Persistent=true` catches up a missed job after downtime. A timer is a pair of unit files.

```bash
# Step 1: the .service -- WHAT to run.
sudo tee /etc/systemd/system/pcie-check.service >/dev/null << 'EOF'
[Unit]
Description=Hourly PCIe Health Check

[Service]
Type=oneshot
ExecStart=/opt/test/pcie_verify.sh
User=testeng
EOF

# Step 2: the .timer -- WHEN to run it.
sudo tee /etc/systemd/system/pcie-check.timer >/dev/null << 'EOF'
[Unit]
Description=Run PCIe health check every hour

[Timer]
OnCalendar=hourly
# Other forms:
#   OnCalendar=*-*-* 06:00:00    -- daily at 06:00
#   OnCalendar=Mon..Fri 07:30    -- weekdays at 07:30
#   OnCalendar=*:0/5             -- every 5 minutes
RandomizedDelaySec=30            # spread firings over a 0-30s window (thundering herd)
Persistent=true                  # if the machine was off, run ASAP on next boot

[Install]
WantedBy=timers.target
EOF

# Step 3: load, enable, start.
sudo systemctl daemon-reload
sudo systemctl enable --now pcie-check.timer

# Inspect:
systemctl list-timers                         # all timers + next/last run times
systemctl status pcie-check.timer
journalctl -u pcie-check.service -n 50 --no-pager
```

---

## Debugging Bash Scripts

```bash
# set -x: PRINT every command (after expansion) before running it. The #1 debug tool.
set -x
speed=$(cat /sys/bus/pci/devices/$BDF/current_link_speed)
set +x                              # turn tracing back off

# Trace from the command line without editing the script:
bash -x ./myscript.sh               # trace execution
bash -v ./myscript.sh               # print each line as READ (before expansion)
bash -n ./myscript.sh               # SYNTAX CHECK ONLY -- catches unclosed quotes/blocks

# Richer trace prefix: show file, line number, and function name:
export PS4='+(${BASH_SOURCE}:${LINENO}): ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'
bash -x ./myscript.sh

# Trace ONLY one function, restore caller's options afterward:
noisy_fn() {
    local saved; saved=$(set +o)    # snapshot current shell options as eval-able string
    set -x
    # ... body under trace ...
    eval "$saved"                   # restore exactly what was set before
}

# shellcheck: STATIC analysis (like pylint for bash). Run it on every script you write.
#   sudo apt install shellcheck
shellcheck ./myscript.sh
# Catches: unquoted variables (SC2086), useless cat, wrong [ ] vs [[ ]], unreachable code,
# $(...) in single quotes, and dozens of subtle quoting/expansion bugs.
# Treat its warnings as errors; clean production scripts pass with zero findings.
```

---

## Manufacturing Test Scripts (Complete, End-to-End)

### pcie_verify.sh -- Topology Matches Expected Config

```bash
#!/bin/bash
# pcie_verify.sh -- verify every expected PCIe device is present at expected speed/width.
# Exit 0 = all pass; exit 1 = any fail. Safe to drive from cron, systemd, or the ATF.
set -euo pipefail

readonly EXPECTED_CONFIG="${1:-/opt/test/expected_topology.json}"
readonly LOG="/var/log/mfg_test/pcie_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }
die() { log "FATAL: $*"; exit 1; }
trap 'log "Script complete (exit $?)."' EXIT

[[ -f "$EXPECTED_CONFIG" ]] || die "Config not found: $EXPECTED_CONFIG"
command -v jq >/dev/null    || die "jq is required but not installed"

# expected_topology.json format:
# [{"bdf":"0000:01:00.0","type":"gpu","speed":"16 GT/s","width":"16"}, ...]
total=0; passed=0; failed=0

# < <(...) keeps counters in THIS shell, not a subshell (the classic bash pipe-subshell trap).
while IFS= read -r device; do
    bdf=$(jq -r '.bdf'   <<< "$device")
    type=$(jq -r '.type' <<< "$device")
    exp_speed=$(jq -r '.speed' <<< "$device")
    exp_width=$(jq -r '.width' <<< "$device")
    (( total++ ))

    if ! lspci -s "$bdf" &>/dev/null; then
        log "FAIL: $type at $bdf NOT FOUND"; (( failed++ )); continue
    fi

    # Read directly from sysfs -- faster and more reliable than parsing lspci text.
    actual_speed=$(< "/sys/bus/pci/devices/$bdf/current_link_speed" 2>/dev/null) || actual_speed="UNKNOWN"
    actual_width=$(< "/sys/bus/pci/devices/$bdf/current_link_width" 2>/dev/null) || actual_width="0"

    if [[ "$actual_speed" == "$exp_speed" && "$actual_width" == "$exp_width" ]]; then
        log "PASS: $type $bdf -> $actual_speed x$actual_width"; (( passed++ ))
    else
        log "FAIL: $type $bdf -> expected ${exp_speed} x${exp_width}, got ${actual_speed} x${actual_width}"
        (( failed++ ))
    fi
done < <(jq -c '.[]' "$EXPECTED_CONFIG")

log "Results: $passed/$total passed, $failed failed"
(( failed == 0 )) || die "PCIe verification FAILED"
log "ALL DEVICES PASSED"
```

### nvme_health.sh -- SMART Pass/Fail Across All NVMe Drives

```bash
#!/bin/bash
# nvme_health.sh -- gate manufacturing pass/fail on NVMe SMART health.
set -euo pipefail
log() { echo "[$(date '+%H:%M:%S')] $*"; }
rc=0

for dev in $(nvme list -o json | jq -r '.Devices[].DevicePath'); do
    log "Checking $dev..."
    smart=$(nvme smart-log "$dev" -o json)

    crit=$(jq '.critical_warning' <<< "$smart")   # 0 = healthy; any bit set = fault flag
    # nvme-cli JSON reports temperature in Kelvin; subtract 273 to get Celsius.
    temp_k=$(jq '.temperature'    <<< "$smart")
    temp=$(( temp_k - 273 ))                      # Celsius
    pct_used=$(jq '.percent_used' <<< "$smart")   # endurance consumed
    media_err=$(jq '.media_errors'<<< "$smart")   # uncorrectable media errors

    # Manufacturing pass criteria for a brand-new board:
    if (( crit != 0 ));     then log "FAIL: $dev critical_warning=$crit"; rc=1; continue; fi
    if (( media_err != 0 )); then log "FAIL: $dev media_errors=$media_err"; rc=1; continue; fi
    if (( temp >= 70 ));     then log "FAIL: $dev temp=${temp}C -- too hot"; rc=1; continue; fi
    if (( pct_used >= 5 ));  then log "WARN: $dev percent_used=${pct_used}% (used drive?)"; fi

    log "PASS: $dev (temp=${temp}C wear=${pct_used}% media_err=$media_err)"
done
exit $rc
```

### link_check.sh -- Single Device, Scriptable Verdict

```bash
#!/bin/bash
# link_check.sh <bdf> [expected_speed] [expected_width]
# Minimal: exits 0 on pass, 1 on fail, 2 on missing device.
set -euo pipefail
BDF="${1:?Usage: $0 <bdf> [speed] [width]}"
EXP_SPEED="${2:-16 GT/s}"
EXP_WIDTH="${3:-16}"
SYS="/sys/bus/pci/devices/$BDF"

[[ -d "$SYS" ]] || { echo "FAIL: $BDF not enumerated"; exit 2; }
speed=$(< "$SYS/current_link_speed")   # < file is a fast read, no cat subprocess
width=$(< "$SYS/current_link_width")

if [[ "$speed" == "$EXP_SPEED" && "$width" == "$EXP_WIDTH" ]]; then
    echo "PASS: $BDF $speed x$width"; exit 0
else
    echo "FAIL: $BDF got $speed x$width, expected $EXP_SPEED x$EXP_WIDTH"; exit 1
fi
```

### aer_cycle.sh -- Clear AER, Stress, Report Errors

```bash
#!/bin/bash
# aer_cycle.sh <bdf> -- arm, apply stress, report which AER error bits re-set.
# Root-cause pattern: clear -> stress -> read. The bits that re-latch ARE the failure mode.
set -euo pipefail
BDF="${1:?need a BDF}"

echo "=== AER BEFORE ==="
for f in /sys/bus/pci/devices/$BDF/aer_dev_correctable \
         /sys/bus/pci/devices/$BDF/aer_dev_fatal \
         /sys/bus/pci/devices/$BDF/aer_dev_nonfatal; do
    [[ -r "$f" ]] && { printf "%-30s\n" "${f##*/}"; cat "$f"; echo; }
done

# Apply stress (traffic generator, GPU load, NVMe fio, thermal soak):
run_stress "$BDF" 60   # 60-second stress run

echo "=== AER AFTER STRESS ==="
lspci -s "$BDF" -vvv | grep -A12 'Advanced Error Reporting' | grep -E 'CESta|UESta'
# A clean board: all status bits clear.
# The SPECIFIC bits that re-set (BadTLP, BadDLLP, RxErr, ReplayTimeout vs.
# uncorrectable Malformed TLP) are the root-cause classification.
```

The sysfs counters are the right long-term polling interface. Each key maps to a PCIe spec error:

```bash
# Read all three AER counter files at once and label them:
BDF=0000:03:00.0
SYS=/sys/bus/pci/devices/$BDF
for f in aer_dev_correctable aer_dev_nonfatal aer_dev_fatal; do
    [[ -r "$SYS/$f" ]] || continue
    echo "--- $f ---"
    cat "$SYS/$f"
done

# Expected output on a healthy device (ALL counts should be zero or absent):
# --- aer_dev_correctable ---
# Receiver Error            0      <- physical layer noise on the lane
# Bad TLP                   0      <- bad Transaction Layer Packet; framing error
# Bad DLLP                  0      <- bad Data-Link Layer Packet
# RELAY_NUM Rollover        0      <- internal counter rollover; not a real error event
# Replay Timer Timeout      0      <- retrain fired; marginal link under load
# Advisory Non-Fatal        0
# Corrected Internal Error  0
# Header Log Overflow       0
# TOTAL_ERR_COR             0

# If any counter is non-zero and rising, classify:
# Growing RxErr / BadTLP -> physical layer: SI problem, marginal trace, bad connector, retimer
# Growing Replay Timer Timeout -> link retrain under traffic; check eye diagram
# Any Fatal error counter -> endpoint was reset; check lspci for link status after the event
```

Polling counters over time to detect a rate (useful for a pass/fail threshold on a production test):

```bash
# Poll an AER correctable counter at T=0 and T=60s; fail if any counter accumulated.
snap() {
    grep -E 'Bad TLP|Replay Timer' "/sys/bus/pci/devices/$1/aer_dev_correctable" \
        | awk '{sum += $2} END {print sum+0}'
}
before=$(snap "$BDF")
run_stress "$BDF" 60
after=$(snap "$BDF")
delta=$(( after - before ))
(( delta == 0 )) || echo "FAIL: $delta correctable AER events during stress (BDF=$BDF)"
```

---

## One-Liner Reference

| Goal | Command |
|---|---|
| Count NVIDIA GPUs present | `lspci -d 10de: \| wc -l` |
| List degraded PCIe links | see loop below |
| Watch live for kernel errors | `dmesg -w \| grep -i 'error\|fail\|reset'` |
| AER correctable count for one BDF | `grep TOTAL /sys/bus/pci/devices/0000:03:00.0/aer_dev_correctable` |
| NVMe temp in Celsius | `printf '%d C\n' "$(( $(nvme smart-log -o json /dev/nvme0 \| jq .temperature) - 273 ))"` |
| Count unique IPs in access log | `awk '{print $1}' access.log \| sort -u \| wc -l` |
| Top 10 largest files recursively | `find . -type f -exec du -h {} + \| sort -rh \| head -10` |
| Lines in file1 not in file2 | `comm -23 <(sort file1) <(sort file2)` |
| Pass rate from a results CSV | `awk -F, '$NF=="PASS"{p++} END{printf "%.1f%%\n", p/NR*100}' r.csv` |
| Average of a column | `awk '{s+=$1;n++} END{printf "%.2f\n",s/n}' nums.txt` |
| Count files by extension | `find . -type f \| sed 's/.*\.//' \| sort \| uniq -c \| sort -rn` |
| Replace in all Python files | `find . -name '*.py' -exec sed -i 's/old_fn/new_fn/g' {} +` |
| Top error messages | `grep ERROR log \| sort \| uniq -c \| sort -rn \| head` |
| Bytes received on eth0 | `cat /sys/class/net/eth0/statistics/rx_bytes` |
| Which process holds port 8080 | `ss -tlnp \| grep :8080` |
| Kill all pytest runners | `pgrep -f pytest \| xargs -r kill -15` |
| Follow log for ERROR lines | `tail -f /var/log/test.log \| grep --line-buffered ERROR` |
| All hwmon sensor temps | `paste <(cat /sys/class/thermal/thermal_zone*/type) <(cat /sys/class/thermal/thermal_zone*/temp) \| awk '{printf "%-20s %.1f C\n", $1, $2/1000}'` |

```bash
# List every PCIe device running below its maximum link speed or width:
for d in /sys/bus/pci/devices/*/; do
    cs=$(cat "$d/current_link_speed" 2>/dev/null) || continue
    ms=$(cat "$d/max_link_speed"     2>/dev/null) || continue
    cw=$(cat "$d/current_link_width" 2>/dev/null)
    mw=$(cat "$d/max_link_width"     2>/dev/null)
    [[ "$cs" == "$ms" && "$cw" == "$mw" ]] && continue
    echo "DEGRADED ${d##*devices/}  speed ${cs}/${ms}  width x${cw}/x${mw}"
done
```
