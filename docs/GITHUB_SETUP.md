# Uploading the project to GitHub

This guide covers the first upload to a **new, empty GitHub repository**. If the remote already has a history, review its branches and changes before applying these steps.

## 1. Check the project directory

Extract `quota-dock-github-1.2.1.zip` to obtain the `quota-dock/` directory. Its `README.md`, `pyproject.toml`, `quota_widget/` and `.github/` should sit at the root of your repository. Upload the extracted source files, not the ZIP as a single source file.

The ZIP does not contain Git history, a remote address or a preconfigured Git identity. Include `.github`, `.gitignore`, `.gitattributes` and `.editorconfig` even if your file manager hides dotfiles.

## 2. Create an empty GitHub repository

Choose **New repository** on GitHub. The suggested name is `quota-dock`. Select the visibility you want, and **do not automatically generate a README, .gitignore or licence**: the project already includes them.

Copy the remote URL shown after creation. Replace `YOUR_GITHUB_ID` below with your GitHub account or organisation name.

## 3. Initialise Git and create the first commit

Starting in the directory that contains the extracted project:

```bash
cd quota-dock
git init -b main
git add .
git status --short
git diff --cached --stat
git diff --cached
git commit -m "Initial import of Quota Dock"
```

If Git has no author identity configured, set **your own chosen name and email address**, then repeat the commit. A public commit can expose its author email. You can use the private email address supplied by GitHub. This project does not set an author identity for you.

Confirm that `.venv/`, `dist/`, `build/`, authentication files and usage-state files are not staged. A `.gitignore` does not automatically untrack files already committed to an existing repository.

## 4. Connect the remote and push

```bash
git remote add origin https://github.com/YOUR_GITHUB_ID/quota-dock.git
git remote -v
git push -u origin main
```

Use your chosen GitHub authentication method if prompted. Do not embed a token in the remote URL or in source files. If you use SSH, use the SSH URL shown by GitHub.

If a push is rejected because the remote already has commits, inspect that history. These instructions do not use a force push to overwrite other work.

## 5. Check the uploaded project

1. Confirm that the repository home page displays the README and demo image.
2. Open **Actions** and check the test and package-build results for both Ubuntu/Python combinations.
3. Add a description, such as `Ubuntu desktop widget for Codex and Claude subscription usage`.
4. Optionally add topics such as `ubuntu`, `pyside6`, `codex`, `claude-code` and `usage-monitor`.

Build artifacts are attached to the Actions run. To distribute a Debian installer, follow the [release guide](RELEASING.md) and attach the reviewed packages to GitHub Releases. The workflows do not automatically publish to an external package registry or PyPI.

## Uploading through the website

GitHub's web upload can also be used, but include **hidden files and the entire `.github/` directory**. Omitting them loses the CI configuration and exclusion rules. Do not add a second `quota-dock/` directory inside the repository root.

## Official documentation

- [GitHub: add locally hosted code](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)
- [GitHub: set your commit email address](https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address)
- [GitHub: manage repository releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)
