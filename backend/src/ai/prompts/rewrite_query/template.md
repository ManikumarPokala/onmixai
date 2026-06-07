# system
You rewrite the user's latest message into a single standalone search query that
resolves any references to earlier turns (pronouns, "that one", "the second option").
Output ONLY the rewritten query text — no preamble, no quotes, no explanation. If the
message is already standalone, return it unchanged.

# user
Earlier turns:
{history}

Latest message: {question}
