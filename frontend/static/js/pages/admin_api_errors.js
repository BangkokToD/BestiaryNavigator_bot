(() => {
  const root = document.querySelector("[data-admin-api-errors]");

  if (!root) {
    return;
  }

  root.addEventListener("click", async (event) => {
    const target = event.target;

    if (!(target instanceof HTMLElement)) {
      return;
    }

    const resolveButton = target.closest("[data-api-error-resolve-url]");
    if (resolveButton instanceof HTMLButtonElement) {
      await resolveApiError(resolveButton);
    }
  });

  async function resolveApiError(button) {
    const url = button.dataset.apiErrorResolveUrl || "";
    const card = button.closest(".bn-api-error-card");
    const message = card?.querySelector("[data-api-error-message]") || null;

    if (!url) {
      setMessage(message, "Endpoint закрытия не найден.", "error");
      return;
    }

    button.disabled = true;
    setMessage(message, "Закрываю ошибку...", "info");

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          Accept: "application/json",
        },
      });
      const payload = await response.json().catch(() => null);

      if (!response.ok) {
        const errorMessage = payload?.detail?.message || payload?.detail || "Ошибка не закрыта.";
        throw new Error(errorMessage);
      }

      if (card instanceof HTMLElement) {
        card.classList.remove("bn-api-error-card--unresolved", "bn-api-error-card--retrying");
        card.classList.add("bn-api-error-card--resolved");
      }

      setMessage(message, "Ошибка помечена как resolved.", "success");
      button.remove();
    } catch (error) {
      const errorText = error instanceof Error ? error.message : "Ошибка не закрыта.";
      setMessage(message, errorText, "error");
      button.disabled = false;
    }
  }

  function setMessage(message, text, state) {
    if (!(message instanceof HTMLElement)) {
      return;
    }

    message.textContent = text;
    message.dataset.state = state;
  }
})();