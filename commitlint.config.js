// Dependabot writes "<type>: Bump <dep> ..." with a capital B, which the conventional
// config's subject-case rule rejects. Observed with a "ci" prefix from the github-actions
// updater (examples b4d15ec) and a "chore(deps-dev)" prefix from the npm updater (stack
// 5f760bd, amfeltlabs/github-actions a015cc0). The predicate below adds a skip only for
// the three Dependabot shapes listed here; commitlint's built-in ignores (merge, revert,
// fixup!/squash! and the like) still apply as before. A Dependabot commit of another shape
// is linted like any other commit:
//   1. Single dependency, title only: "<type>: Bump <dep> from <a> to <b>", optionally
//      followed by "in the <group> group" or "in /<directory>".
//   2. Grouped update, title only: "<type>: Bump the <group> group with <n> updates",
//      optionally "across <n> directories".
//   3. One-dependency grouped update: the subject is "<type>: Bump <dep>", optionally
//      followed by "in the <group> group" or "in /<directory> in the <group> group" (a
//      directory only together with a group), with no "from ... to ...", AND the body's
//      first paragraph starts with "Bumps the <group> group with <n> update" (or "Bumps the
//      <group> group in /<directory> with <n> update"). Both parts must match; a bare
//      "Bump <dep>" subject with any other body is linted. A message forged to match both
//      parts is skipped too, since the predicate reads only the message text.
const type = '[a-z]+(\\([a-z-]+\\))?'
const dependabotTitle = new RegExp(
  `^${type}: Bump (` +
    // Single dependency, optionally scoped to a group or a directory.
    '\\S+ from \\S+ to \\S+( in (the \\S+ group|/\\S*))?' +
    '|' +
    // Grouped update.
    'the \\S+ group( across \\d+ director(?:y|ies))? with \\d+ updates?' +
    ')(\\r?\\n|$)'
)
// One-dependency grouped update: subject has no version range, so the body's first
// paragraph is what identifies it as Dependabot's.
const dependabotOneUpdateGroup = new RegExp(
  `^${type}: Bump \\S+(( in /\\S*)? in the \\S+ group)?\\r?\\n\\r?\\n` +
    'Bumps the \\S+ group( in /\\S*)? with \\d+ update'
)

module.exports = {
  extends: ['@commitlint/config-conventional'],
  ignores: [(message) => dependabotTitle.test(message) || dependabotOneUpdateGroup.test(message)]
}
