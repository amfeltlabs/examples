// Dependabot writes "<type>: Bump <dep> ..." with a capital B, which the conventional
// config's subject-case rule rejects. The github-actions updater does this regardless of
// commit-message.prefix. The predicate below matches exactly three shapes and nothing
// broader, so every human commit, and any Dependabot commit of another shape, is still
// fully linted:
//   1. Single dependency: "<type>: Bump <dep> from <a> to <b>", optionally followed by
//      "in the <group> group" or "in <directory>".
//   2. Grouped update: "<type>: Bump the <group> group with <n> updates", optionally
//      "across <n> directories".
//   3. One-dependency grouped update: the subject is "<type>: Bump <dep>" with no
//      "from ... to ...", and the body's first paragraph starts with
//      "Bumps the <group> group with <n> update". A bare "Bump <dep>" subject with any
//      other body is not ignored.
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
  `^${type}: Bump \\S+\\r?\\n\\r?\\nBumps the \\S+ group with \\d+ update`
)

module.exports = {
  extends: ['@commitlint/config-conventional'],
  ignores: [(message) => dependabotTitle.test(message) || dependabotOneUpdateGroup.test(message)]
}
