/* Copyright (C) 2025, 2026  Marcel Hellkamp
 * SPDX-License-Identifier: AGPL-3.0-or-later */

// Keep every form token in sync when htmx rotates the CSRF cookie.
document.body.addEventListener("csrfRotated", (event) => {
  const token = event.detail && event.detail.value;
  if (!token) return;

  for (const input of document.querySelectorAll('input[name="csrf"]')) {
    input.value = token;
  }
});
