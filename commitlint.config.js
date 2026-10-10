// Dependabot writes "<type>: Bump <dep> from <a> to <b>" with a capital B, which the
// conventional config's subject-case rule rejects. The github-actions updater does this
// regardless of commit-message.prefix. The predicate below matches that exact title shape
// (single dependency, or a grouped update) and nothing broader, so every human commit,
// and any Dependabot commit of another shape, is still fully linted.
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

module.exports = {
  extends: ['@commitlint/config-conventional'],
  ignores: [(message) => dependabotTitle.test(message)]
}
