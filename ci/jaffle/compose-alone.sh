# shellcheck shell=sh
# Sourced by fresh-clone.sh and tutorial.sh, which define say and die. It decides whether a
# check of the reader's Compose path applies to the ref under test, and holds the one step
# those checks share.
#
# CI can test a ref older than the commit it runs from: a dispatch runs these scripts on a
# release tag. A tag from before that path cannot pass its checks: its compose.yaml needs a
# .env and sets the DuckDB shell's user, and its reset removes work/. The ref's own history
# tells, not its tree and not a list of versions: work/warehouse/.gitkeep arrived with that
# path, so a ref whose history has never held the file predates it. Every later ref
# descends from the commit that added it, so no change to a later tree turns a check into a
# skip: with the file deleted, the checks run and fail.

# predates_compose_alone JAFFLE_DIR: JAFFLE_DIR's ref has never held the file. A shallow
# clone is refused: its history is cut off, so it cannot tell. --full-history keeps a merge
# that removes the file from hiding the side of the history that added it.
predates_compose_alone() {
    [ "$(git -C "$1" rev-parse --is-shallow-repository)" = false ] \
        || die "$1 is not in a full clone, so its history cannot tell which checks apply; clone it with fetch-depth: 0, or run git fetch --unshallow"
    [ -z "$(git -C "$1" log -1 --full-history --format=%h -- work/warehouse/.gitkeep)" ]
}

# make_private DIR: leave DIR readable by its owner only, as a umask of 077 would, or stop:
# a check of a reader with that umask proves nothing on a file others can read.
make_private() {
    chmod -R go-rwx "$1"
    [ -z "$(find "$1" -perm -040 -o -perm -004)" ] || die "$1 is still readable by others"
}

# compose_alone_check JAFFLE_DIR WHAT COMMAND...: run COMMAND, the check WHAT, unless the
# ref in JAFFLE_DIR predates the reader's Compose path; then say that WHAT is skipped.
compose_alone_check() {
    ca_dir=$1 ca_what=$2
    shift 2
    if predates_compose_alone "$ca_dir"; then
        say "SKIPPED: $ca_what: the history of this ref has never held work/warehouse/.gitkeep, so it predates the reader's Compose path"
    else
        "$@"
    fi
}

ca_git() {
    git -C "$ca_repo" -c user.name=ci -c user.email=ci@example.invalid -c commit.gpgsign=false "$@"
}

ca_mark() {
    ca_ran=1
}

# compose_alone_controls DIR: negative controls on the decision, in throwaway repositories
# under DIR. A ref that never held the file is skipped, and says so. A ref that held it is
# not, even after a merge of an older branch removed the file from its tree and made that
# merge identical to the older branch. A shallow clone of that ref is refused.
compose_alone_controls() {
    ca_repo=$1/repo
    git init -q "$ca_repo"
    ca_git commit -q --allow-empty -m base

    ca_ran=0
    compose_alone_check "$ca_repo" "the control" ca_mark >"$1/out"
    if [ "$ca_ran" -ne 0 ] || ! grep -q 'SKIPPED: the control: ' "$1/out"; then
        die "negative control: a ref that never held work/warehouse/.gitkeep was not skipped"
    fi

    ca_git checkout -q -b older
    ca_git commit -q --allow-empty -m older
    ca_git checkout -q -
    mkdir -p "$ca_repo/work/warehouse"
    : >"$ca_repo/work/warehouse/.gitkeep"
    ca_git add work/warehouse/.gitkeep
    ca_git commit -q -m 'add the file'
    ca_git merge -q --no-ff --no-commit older >/dev/null 2>&1 \
        || die "negative control: the merge in the throwaway repository failed"
    ca_git rm -q work/warehouse/.gitkeep
    ca_git commit -q -m 'merge the older branch and remove the file'

    ca_ran=0
    compose_alone_check "$ca_repo" "the control" ca_mark >"$1/out"
    [ "$ca_ran" -eq 1 ] || die "negative control: a ref whose history held work/warehouse/.gitkeep was skipped"

    git clone -q --depth 1 "file://$ca_repo" "$1/shallow"
    if (compose_alone_check "$1/shallow" "the control" :) >"$1/out" 2>&1; then
        die "negative control: a shallow clone was not refused"
    fi
    grep -q 'not in a full clone' "$1/out" || die "negative control: a shallow clone failed for another reason"
    echo "ok: negative controls: only a ref that never held the file is skipped, and a shallow clone is refused"
}

# steps_dir_controls DIR STEPS_DIR_SH: negative controls on that script, in a throwaway tree
# under DIR. A tag's jaffle/steps is found while it is the only one, ci/jaffle/steps wins
# beside it, and a tree with neither is refused.
steps_dir_controls() {
    sd_tree=$1/layout
    mkdir -p "$sd_tree/jaffle/steps"
    : >"$sd_tree/jaffle/steps/01-start.sh"
    sd_got=$(sh "$2" "$sd_tree/jaffle") || die "negative control: steps-dir.sh refused a tag's layout"
    [ "$sd_got" = "$(cd "$sd_tree/jaffle/steps" && pwd)" ] || die "negative control: steps-dir.sh chose $sd_got for a tag's layout"

    mkdir -p "$sd_tree/ci/jaffle/steps"
    : >"$sd_tree/ci/jaffle/steps/01-start.sh"
    sd_got=$(sh "$2" "$sd_tree/jaffle") || die "negative control: steps-dir.sh refused both layouts"
    [ "$sd_got" = "$(cd "$sd_tree/ci/jaffle/steps" && pwd)" ] || die "negative control: steps-dir.sh chose $sd_got beside ci/jaffle/steps"

    rm "$sd_tree/ci/jaffle/steps/01-start.sh" "$sd_tree/jaffle/steps/01-start.sh"
    if sd_got=$(sh "$2" "$sd_tree/jaffle" 2>"$1/out"); then
        die "negative control: steps-dir.sh accepted a tree with no steps: $sd_got"
    fi
    grep -q 'holds no steps' "$1/out" || die "negative control: steps-dir.sh failed for another reason"
    echo "ok: negative controls: steps-dir.sh finds either layout, prefers ci/jaffle/steps, and refuses neither"
}

# reader_files_naming_steps JAFFLE_DIR: the files a reader opens that name the steps' path.
reader_files_naming_steps() {
    (cd "$1" && grep -lsE 'steps/|ci/jaffle' ../README.md README.md compose.yaml .env.example project/README.md) || true
}

# reader_files_controls DIR: negative controls on that check, in a throwaway tree under DIR:
# a README that names no path passes, and each of the two paths is found.
reader_files_controls() {
    rf_tree=$1/reader/jaffle
    mkdir -p "$rf_tree"
    echo 'The steps of the tutorial.' >"$rf_tree/README.md"
    [ -z "$(reader_files_naming_steps "$rf_tree")" ] || die "negative control: a README that names no path was found"
    for rf_path in steps/01-start.sh ci/jaffle/tutorial.sh; do
        echo "$rf_path" >"$rf_tree/README.md"
        [ -n "$(reader_files_naming_steps "$rf_tree")" ] || die "negative control: a README naming $rf_path was not found"
    done
    echo "ok: negative controls: the check finds a file a reader opens that names either path"
}
