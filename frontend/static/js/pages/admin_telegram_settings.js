(() => {
  const root = document.querySelector("[data-admin-telegram-settings]");

  if (!root) {
    return;
  }

  root.addEventListener("click", async (event) => {
    const target = event.target;

    if (!(target instanceof HTMLElement)) {
      return;
    }

    const copyButton = target.closest("[data-copy-command]");
    if (copyButton instanceof HTMLButtonElement) {
      await copyCommand(copyButton);
      return;
    }

    const testButton = target.closest("[data-route-test-url]");
    if (testButton instanceof HTMLButtonElement) {
      await postRouteAction(testButton, "Тестовое уведомление отправлено.");
      return;
    }

    const disableButton = target.closest("[data-route-disable-url]");
    if (disableButton instanceof HTMLButtonElement) {
      await postRouteAction(disableButton, "Маршрут отключён.", true);
    }
  });

  async function copyCommand(button) {
    const command = button.dataset.copyCommand || "";

    if (!command) {
      setButtonText(button, "Команда не найдена");
      return;
    }

    try {
      await navigator.clipboard.writeText(command);
      setButtonText(button, "Скопировано");
    } catch {
      setButtonText(button, "Скопируй вручную");
    }
  }

  async function postRouteAction(button, successText, markDisabled = false) {
    const url = button.dataset.routeTestUrl || button.dataset.routeDisableUrl || "";
    const routeRow = button.closest("[data-route-id]");
    const message = routeRow?.querySelector("[data-route-message]") || null;

    if (!url) {
      setRouteMessage(message, "Endpoint не найден.", "error");
      return;
    }

    button.disabled = true;
    setRouteMessage(message, "Выполняю действие...", "info");

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          Accept: "application/json",
        },
      });
      const payload = await response.json().catch(() => null);

      if (!response.ok) {
        const errorMessage = payload?.detail?.message || payload?.detail || "Действие не выполнено.";
        throw new Error(errorMessage);
      }

      setRouteMessage(message, successText, "success");

      if (markDisabled && routeRow instanceof HTMLElement) {
        routeRow.classList.add("bn-route-row--disabled");
        routeRow.querySelectorAll("[data-route-test-url], [data-route-disable-url]").forEach(
          (actionButton) => {
            if (actionButton instanceof HTMLButtonElement) {
              actionButton.disabled = true;
            }
          }
        );
      } else {
        button.disabled = false;
      }
    } catch (error) {
      const errorText = error instanceof Error ? error.message : "Действие не выполнено.";
      setRouteMessage(message, errorText, "error");
      button.disabled = false;
    }
  }

  function setButtonText(button, text) {
    const initialText = button.dataset.initialText || button.textContent || "";

    if (!button.dataset.initialText) {
      button.dataset.initialText = initialText;
    }

    button.textContent = text;

    window.setTimeout(() => {
      button.textContent = button.dataset.initialText || initialText;
    }, 1800);
  }

  function setRouteMessage(message, text, state) {
    if (!(message instanceof HTMLElement)) {
      return;
    }

    message.textContent = text;
    message.dataset.state = state;
  }
})();