# AGENTS.md

- Before you finish, the asset validation in `tools/` must pass, and
  rebuilding the assets twice must give the same manifest.
- Generated files are never edited by hand; change their generator instead.
- This specification covers interoperability only: what goes on the wire
  and how a peer must react. Local behaviour that a peer can neither observe
  nor rely on does not belong here.
- A zmux endpoint talking to a plain QMux peer must remain a QMux-02
  endpoint.
- State each requirement once, with BCP 14 keywords, and reference it
  elsewhere. Give a reason only where a rule would otherwise be misread.
- A change to a wire rule updates the normative text, the registry and the
  test vectors together.
- Codepoints are assigned only as the registry describes; GREASE values are
  never assigned.
- Examples are informative; the normative text prevails.
- The documents describe the current protocol only, without change history.
- Commit subjects are short, English and imperative.
- Never commit host names, IP addresses or credentials.
