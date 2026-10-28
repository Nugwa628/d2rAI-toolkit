# Owner update: how the coordinator reports to the human

The owner is not reading code. Keep updates short, plain and the same shape every time. Send one when something is
finished, when you need a decision, or after a long quiet period. Do not narrate every step.

## Shape

```
**Done**
- <what was merged or released, with bug numbers: "Merged fix for #17, released 1.4.2">

**Running**
| Lane | Job | Waiting for |
|---|---|---|
| bug-18-icons | Skill icons missing on page 2 | game slot 3 |
| inv-items | Count items per type | nothing, finishing |

**Need from you**
<A decision or an action. One clear question with a recommendation, or a copy-paste block.>
```

## Copy-paste blocks

When the human has to run something, give the exact command, where to run it, and what they should see:

````
Run this in a terminal (PowerShell) from the repo folder:

    python -m modkit build my-mod

You should see: `0 errors`. If you see anything else, paste the last 10 lines back here.
````

## Rules for good updates
- Lead with the result, not the process.
- Use a table for more than two items of status.
- Say what you decided on your own ("I used the default name `Foo`; tell me to change it") instead of asking about
  every small thing.
- Ask before anything public, paid or destructive: publishing a release, making a repo public, a force-push,
  deleting something, spending money, touching the real game install. Say what will happen and offer the exact
  action.
- Never ask for a token, password or key in chat. Ask the owner to put it in a file in their home folder, and give the
  file name, or use the tool's private input.
- When you are blocked, say so in the first line, and say what unblocks you.
- When something went wrong, say what, say what it affected, and say what you did. Do not hide it in the middle.
- Keep a short notes file with their decisions (name, hosting, what to drop), so you don't ask twice.

## Example

> **Done**
> - Merged the fix for #17 (crash entering the Catacombs) and #19 (same cause). Released 1.4.2. Release page is live.
>
> **Running**
> | Lane | Job | Waiting for |
> |---|---|---|
> | bug-20-vendor | Vendors sell the wrong potions | game slot |
>
> **Need from you**
> Players report #21 (a missing sound at the Blood Moor). I think it is the same as #12. Do you want me to merge them
> and fix once? (I recommend yes.)
