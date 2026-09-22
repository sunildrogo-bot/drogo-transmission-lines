# Admin project-progress dashboard

The Admin dashboard now follows the actual operating workflow:

`Admin upload -> SME review -> Inspection Done -> visible to Client`

Each project displays:

- total towers;
- towers not yet captured/uploaded;
- towers containing Admin-uploaded images;
- total uploaded image count;
- towers pending SME review;
- Inspection Done / Client-visible towers;
- open and closed defects; and
- overall inspection-completion percentage.

There is no Pilot-upload stage and no Admin-approval stage. Clicking the
project name opens that project's divisions. No database migration or `.env`
change is required for this dashboard update.
