# SME pending-review workflow

The SME dashboard now follows the operating workflow:

`Admin uploads -> SME reviews RGB -> SME reviews/measures thermal -> SME marks Inspection Done -> Client sees`

It displays assigned lines, Admin-uploaded towers, pending-review towers,
completed/Client-visible towers, RGB image counts, measured/unmeasured thermal
image counts, pending tower labels and progress based on uploaded towers.

The dashboard defaults to Pending Review and also provides Completed and All
Assigned filters. Each card opens the assigned line through Continue Review.

When an SME marks Inspection Done, a confirmation clearly states that the
tower's RGB defects and thermal measurements become visible to the Client
immediately. There is no Admin approval stage. This update requires no database
migration and does not change `.env`.
