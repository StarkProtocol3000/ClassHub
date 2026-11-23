# CLASSHUB — Online-ready Flask application

This package preserves the original CLASSHUB pages and workflows and adds deployment support for:
- Render free web service (website hosting)
- Neon free PostgreSQL (persistent student and lecturer data)
- Secure session-cookie settings and CSRF protection on forms
- Gunicorn production server
- Automatic database schema creation on startup

## Free-tier notes

- Render Free web services can sleep after 15 minutes without traffic; the first visit after sleep may be delayed.
- Render Free web services have temporary local filesystems. CLASSHUB uses PostgreSQL when `DATABASE_URL` is set.
- Neon Free has usage quotas, including 1 GB storage and monthly compute limits. Data persists in the database, but service availability depends on provider limits.
- Free hosting is suitable for a pilot/class project, not guaranteed production-grade availability. Back up the database regularly.

## Deploy overview

1. Create a free Neon project and copy its PostgreSQL connection string. Use the pooled connection string if Neon provides one.
2. Create a GitHub repository and upload the contents of this folder (not the outer ZIP).
3. In Render, create a new Web Service and connect the GitHub repository.
4. Select the Free instance.
5. Build command: `pip install -r requirements.txt`
6. Start command: `gunicorn app:app`
7. Add `DATABASE_URL` with the Neon connection string.
8. Add `CLASSHUB_SECRET` with a long random secret. Render can generate one when using the included Blueprint.
9. Deploy. Open the generated `onrender.com` address and create the first lecturer/admin account at `/setup`.

## Local Windows use

Run `run_windows.bat` as before. If `DATABASE_URL` is not set, the app continues to use the local `classhub.db` SQLite file.

## Existing data

The supplied ZIP did not contain a `classhub.db` database, so there are no existing student records in this package to migrate. If you have a separate `classhub.db` file with real records, do not overwrite it; arrange a migration before deploying.

## Security

- Keep `DATABASE_URL` and `CLASSHUB_SECRET` private. Never put them in source code or send them in chat.
- Use a unique strong password for the first lecturer account.
- The first account created through `/setup` becomes administrator; setup closes once an account exists.
- Make regular database backups and restrict lecturer credentials.
