# Installing Prerequisites on Windows

Do these in order. Every step below uses PowerShell — press the
**Windows key**, type `PowerShell`, and open it (regular, not Admin,
unless a step says otherwise).

---

## 1. Install Git

1. Go to https://git-scm.com/downloads/win and download the installer.
2. Run it. You can click **Next** through almost every screen with
   defaults — the two settings worth choosing deliberately:
   - **"Adjusting your PATH environment"** → choose **"Git from the
     command line and also from 3rd-party software"** (this is usually
     already the recommended/default option)
   - Everything else: defaults are fine
3. Finish the install, then **close and reopen PowerShell** (it needs a
   fresh window to pick up the new PATH).
4. Verify:
   ```powershell
   git --version
   ```
   You should see something like `git version 2.45.0.windows.1`.

5. Tell Git who you are (needed before your first commit):
   ```powershell
   git config --global user.name "Klifford"
   git config --global user.email "your-github-email@example.com"
   ```
   Use the same email as your GitHub account.

---

## 2. Install Python

1. Go to https://www.python.org/downloads/ and click the big **Download
   Python 3.x.x** button.
2. Run the installer. **This step matters — don't skip it:** on the very
   first screen, check the box at the bottom that says
   **"Add python.exe to PATH"** before clicking **Install Now**.
3. Let it finish, then **close and reopen PowerShell** again.
4. Verify:
   ```powershell
   python --version
   ```
   If that says `Python 3.x.x`, you're good. If PowerShell says it can't
   find `python`, the PATH checkbox got missed — rerun the installer,
   choose **Modify**, and make sure "Add to PATH" is checked.

5. Verify `pip` (Python's package installer) came with it:
   ```powershell
   pip --version
   ```

---

## 3. Install Tesseract OCR

This is the actual OCR engine the scraper uses to read scanned PDF pages
(Python's `pytesseract` package is just a wrapper around it).

1. Go to https://github.com/UB-Mannheim/tesseract/wiki
2. Download the latest 64-bit installer (`tesseract-ocr-w64-setup-*.exe`)
3. Run it with defaults. Note the install path it shows you — it's
   normally:
   ```
   C:\Program Files\Tesseract-OCR
   ```
4. **Add it to your PATH manually** (the installer doesn't always do
   this):
   - Press Windows key, type `environment variables`, open **"Edit the
     system environment variables"**
   - Click **Environment Variables...**
   - Under **System variables**, select **Path** → **Edit** → **New**
   - Paste in: `C:\Program Files\Tesseract-OCR`
   - Click OK on all three windows
5. **Close and reopen PowerShell**, then verify:
   ```powershell
   tesseract --version
   ```
   You should see `tesseract 5.x.x`.

---

## 4. (Optional but recommended) Install VS Code

Makes it much easier to open the project folder, edit `fetch.py`'s
selectors, and run commands from an integrated terminal.

1. https://code.visualstudio.com/download → download the Windows
   installer, run it with defaults.
2. Once installed, you can right-click the unzipped project folder in
   File Explorer and choose **"Open with Code"**.

---

## 5. Final check — run all four together

Open a fresh PowerShell window and run:

```powershell
git --version
python --version
pip --version
tesseract --version
```

If all four print a version number with no errors, you're ready to move
on to unzipping the project and running the local test (Step 2 in
INSTALL.md).

### Common snags

- **"'python' is not recognized..."** → the PATH checkbox was missed
  during install (see step 2.4 above), or you didn't reopen PowerShell.
- **"'git' is not recognized..."** → same idea — reopen PowerShell, or
  reinstall and confirm the PATH option.
- **PowerShell blocks running scripts** (you'll see something about
  "execution policy" when activating a Python virtual environment later)
  → run PowerShell **as Administrator** once and enter:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```
  Type `Y` to confirm, then reopen a normal (non-admin) PowerShell window.
