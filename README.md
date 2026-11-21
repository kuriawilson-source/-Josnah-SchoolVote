# SchoolVote

A Django-based school election starter app for up to 2,000 learners. It includes staff-only election administration and official-assisted voting, CSV/XLSX learner imports, duplicate-vote prevention, anonymous ballots, results, and Excel/PDF reports.

## Run locally (Windows PowerShell)

From this folder, run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Open `http://127.0.0.1:8000/`. Administrators sign in at `/login/`; electoral officials sign in separately at `/official/login/`. Voting is assisted by an official at the voting station; learners do not use a public voting booth.

For a school computer lab on the same trusted network, run the app on a school-hosted computer and bind it to the local network:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

Set `DJANGO_ALLOWED_HOSTS` to include the server's local hostname/IP and allow TCP port 8000 through the host firewall. The development server is for evaluation only; deploy with a production WSGI server and HTTPS before handling real votes. Local-network voting continues to work during an internet outage as long as the school host and LAN remain available. This starter does not support voting on disconnected computers followed by later synchronization.

### Share with another Windows computer on the school network

1. Copy the project folder to the host computer, including `db.sqlite3` if it must continue with the current learners, accounts, elections, and voting records. Keep that database private: it contains sensitive school and election data. Do not send it through a public file-sharing link.
2. Install Python 3 on the host computer, then open PowerShell in the copied project folder.
3. Run `.\run-schoolvote-lan.ps1`. On first run, the script creates `.venv`, installs the dependencies, applies database migrations without replacing the existing data, and starts the development server on the school network.
4. If Windows Firewall asks, allow Python on a **Private** network. On each other computer, open one of the `http://<host-ip>:8000/` addresses printed by the script. The host computer must stay on and connected while the app is in use.
5. Keep the database on the host computer. Do not copy the database while the server is running, and do not run separate copies of the same election database on several computers; ballots do not synchronize.

The bundled `db.sqlite3` includes real learner and election information. Transfer the project only to authorized staff using an approved private method. The Django development server is suitable only for evaluation on a trusted school LAN, not for public internet access or a high-stakes production election.

## First election

1. Sign in as an administrator.
2. Import eligible learners from the **Learners** page. Required CSV/XLSX headers are `Adm`, `Name`, `Grade`, and `Stream` (header order is flexible).
3. Create separate electoral official accounts on the **Officials** page and share their login details securely. Administrators can edit official usernames, reset passwords to `12345678`, and delete accounts from this page.
4. Create an election, add positions and at least two candidates per position.
5. Open the election. The official signs in at `/official/login/`, selects **Official voting**, enters the learner's Admission No., confirms the displayed name, grade, and stream with the learner, then records the learner's selections.
6. Officials can open **Voting progress** to view the turnout total and live candidate vote-percentage graphs for each position. Candidate totals and percentages are visible while voting is open and after it closes; officials cannot access election administration. Administrators can close the election to unlock the full results dashboard and Excel/PDF exports.

To end an election, an administrator opens it from the dashboard and chooses **Close voting** on its management page. An election can be deleted from that page only when it has no ballots or participation records; deletion also removes its positions and candidates.

After an election is closed, administrators can open **Print candidate vote ranking** to print a PDF report of candidates ordered from highest votes received to lowest. Ties are listed by position and candidate name. The report is unavailable while voting is open or in draft.

Only signed-in electoral official accounts can use official voting and view turnout and candidate graphs. Administrators provision these accounts separately from administrator accounts. After the official enters an Admission No., the app shows the learner's name, grade, stream, and candidate ballot. If the learner has voted already, a dedicated **Already Voted** screen blocks another attempt. A participation record marks each learner as voted for that election; a unique database constraint prevents concurrent duplicate ballots. Ballots and eligibility records are kept in separate tables. There is no database relationship between a learner and their ballot, and the records do not store vote timestamps. Officials can see candidate tallies during the election, which may influence voting; use this mode only when live tally visibility is intended. This reduces direct linkage in the application data but is not a substitute for an independent election-security audit or a controlled election environment.

Administrators can add or edit individual learner records from the **Learners** page. Admission numbers cannot be changed after creation. Learner records cannot be edited or deleted after the learner has voted in an open election. From the edit page, an administrator can delete a learner who has not voted in an open election. If the learner has participation history, deletion removes them from the active learner roll while retaining closed-election participation records and ballots; learners with no voting history are permanently deleted. Removing a learner from the active roll makes them ineligible across elections. Administrators can re-enable disabled learners from the Learners page. Password resets set the official password to the fixed default `12345678`; this is a shared, known password, so communicate reset credentials securely.

Administrators can add, edit, disable, or enable candidates while an election is a draft or open. Adding candidates to an open election affects only ballots opened after the candidate is added; ballots already cast and recorded votes remain unchanged. Disabled candidates are omitted from new ballots. An open election must retain at least two active candidates for each position. Candidates with recorded votes cannot be deleted, and candidate changes are locked after an election is closed. Editing a candidate's name changes the name displayed alongside historical votes. Positions can only be added while an election is a draft.

Administrators can upload or replace a candidate passport photo from the candidate's **Edit** page. Supported image formats are those accepted by Pillow; uploads are limited to 5 MB. Photos are stored in the `media` folder, displayed on ballots and election setup, and must be kept with `db.sqlite3` when transferring or backing up the app.

Administrators can change their password or register a recovery email from the signed-in navigation. The **Forgot username or password?** link on the administrator sign-in page sends a one-time password-reset link to the saved email; the email includes the username, but never a password. The administrator chooses a new password through that link before signing in. To enable delivery, configure `DJANGO_EMAIL_HOST`, `DJANGO_EMAIL_PORT`, `DJANGO_EMAIL_HOST_USER`, `DJANGO_EMAIL_HOST_PASSWORD`, `DJANGO_EMAIL_USE_TLS`, and `DJANGO_DEFAULT_FROM_EMAIL` in the host environment. Keep mail credentials out of source control and use a provider-issued SMTP/app password. If email delivery fails, the server logs the error; the recovery page intentionally gives the same response for known and unknown addresses.

Draft positions can optionally be restricted to a specific grade and stream; choose both values to create a grade-stream voting group. Unrestricted positions appear to every learner. A learner sees and votes only on positions available to their grade and stream. Each active position requires at least two active candidates before an eligible learner can receive a ballot.

## Production checklist

- Set `DJANGO_DEBUG=0`, use a strong secret in `DJANGO_SECRET_KEY`, and set `DJANGO_ALLOWED_HOSTS` to exact hostnames.
- Configure HTTPS, secure session/CSRF cookies, a production WSGI server, database backups, and a maintained database such as PostgreSQL for a real deployment.
- Set `DJANGO_TIME_ZONE` to the school's timezone before using election opening/closing times.
- Restrict administrator accounts and official voting stations to authorized election staff. Officials should verify the learner's identity before entering their Admission No.
- Test restore and incident-response procedures before an official election. This starter is not independently certified for high-stakes or legally regulated elections.

## Tests

```powershell
.\.venv\Scripts\python.exe manage.py test
```
