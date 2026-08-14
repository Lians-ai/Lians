# A market-research memory walkthrough for first-time users

This walkthrough shows how a nontechnical researcher or marketing student can
test Lians in Cursor without using a real company, private research, an API key,
or a paid Lians account. It takes about five minutes after installation.

You will use a fictional company named **Northstar Nook** and a fictional study
planning app named **SprigNote**. The goal is to prove that one useful research
rule can survive a new chat, that an outdated project fact can be corrected,
and that selected memory can be inspected and permanently removed.

## Before you begin

Use the [official Lians for Cursor guide](../../integrations/cursor/) and choose
its one-click local installer. Review the configuration in Cursor before
approving it, then restart Cursor if it asks you to.

The local setup:

- stores memory on your computer in `~/.lians/mcp.db` by default;
- needs no Lians account or Lians API key; and
- does not change the AI model you selected in Cursor.

Use the same project folder for each chat in this walkthrough. On a clean
machine, the first memory operation may spend a few minutes downloading and
initializing a local semantic model.

## 1. Remember the research context in chat one

Open a new Cursor chat in your test project and paste this prompt:

```text
Use Lians to remember this project research rule: For the synthetic Northstar
Nook project, use only public sources published in 2025 or later and label any
inference as an inference.
```

Approve the `remember` tool if Cursor asks. Then save one fictional project
fact:

```text
Use Lians to remember this synthetic project fact: Northstar Nook currently
plans to launch SprigNote in April 2027.
```

A successful result shows that Cursor called the Lians memory tool and that
Lians accepted each memory. The exact prose may differ by model. If Cursor only
repeats the text without calling a tool, ask it explicitly to call the Lians
`remember` tool.

## 2. Recall it in a separate chat

Close the first chat and open a new chat in the same project folder. Paste:

```text
Before doing any research, use Lians to recall the source rule and planned
launch month for the synthetic Northstar Nook project. Do not browse the web or
invent additional facts.
```

The test passes when Cursor calls Lians and brings back the public-source rule
and April 2027 launch plan from memory. The response should use the small
relevant memories, not reproduce the first chat.

## 3. Correct the outdated project fact

In the second chat, paste:

```text
The synthetic launch plan changed. Use Lians to find the April 2027 memory and
correct it to: Northstar Nook currently plans to launch SprigNote in September
2027. Keep the April version as superseded history, not as the current fact.
```

Then verify the current answer:

```text
Use Lians to recall the current planned launch month for SprigNote. Do not use
the surrounding chat as the source of truth.
```

Success means September 2027 is current. April 2027 may remain visible as
history when you deliberately inspect superseded memory, but it should not be
returned as a second current launch plan.

## 4. Inspect what Lians stored

Paste:

```text
Use Lians to list the Northstar Nook memories, including current and superseded
items. Show each memory ID, its content, and whether it is current or
superseded. Include source metadata only when Lians actually provides it.
```

The output should let you distinguish the current launch fact, its older
version, and the separate research rule. Save the ID of the research-rule
memory for the next step.

## 5. Confirm permanent deletion

First ask Cursor to prepare the operation without deleting anything:

```text
Prepare to permanently forget only the Northstar Nook public-source research
rule. Show me the exact memory content and ID you intend to erase, then wait
for my explicit confirmation. Do not delete the launch-plan memories.
```

Check the displayed content and ID. If they are correct, paste the confirmation
below after replacing `<memory-id>` with the real ID:

```text
I confirm permanent deletion of memory ID <memory-id>. Call the Lians
forget_memory tool with confirm=true for that ID only.
```

Lians requires explicit confirmation for this destructive operation. Do not
confirm if Cursor shows the wrong memory.

## 6. Verify the deleted rule stays gone

Open one more new chat in the same project and paste:

```text
Use Lians to recall any current source or publication-date rule for the
synthetic Northstar Nook project. If Lians returns no such current memory, say
that no current research rule was found. Do not reconstruct the rule from this
prompt or from chat history.
```

The deletion test passes when Lians returns no current research-rule memory.
The September 2027 launch fact can remain because you deleted only the rule.

When you finish, you may use `list_memories` to find the remaining synthetic
Northstar Nook IDs and forget them one at a time with the same preview and
confirmation process.

## Privacy and academic-use note

Use synthetic information while learning the workflow. Do not store
credentials, API keys, respondent data, private research, personal identifiers,
confidential client material, or graded work. Lians does not override your
school's academic-integrity rules, research-ethics requirements, or the data
policies of the AI client you use.

## Share the result

For a workshop version, see the
[student and community kit](../../docs/student-community-kit.md). To report your
setup time, score, or confusing step, reply to the
[three-minute memory challenge](https://github.com/Lians-ai/Lians/discussions/122).
