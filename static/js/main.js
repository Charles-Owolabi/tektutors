document.addEventListener("DOMContentLoaded", () => {
    document.documentElement.classList.add("js-ready");

    const menuToggle = document.getElementById("menuToggle");
    const mainMenu = document.getElementById("mainMenu");

    if (menuToggle && mainMenu) {
        menuToggle.addEventListener("click", () => {
            const isOpen = mainMenu.classList.toggle("is-open");
            menuToggle.setAttribute("aria-expanded", String(isOpen));
        });

        mainMenu.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", () => {
                mainMenu.classList.remove("is-open");
                menuToggle.setAttribute("aria-expanded", "false");
            });
        });
    }

    const registrationForm = document.getElementById("registration-form");
    if (registrationForm && window.location.hash === "#registration-form") {
        const alignRegistrationForm = () => {
            registrationForm.scrollIntoView({ behavior: "auto", block: "start" });
        };

        requestAnimationFrame(alignRegistrationForm);
        window.setTimeout(alignRegistrationForm, 80);
        window.addEventListener("load", () => {
            window.setTimeout(alignRegistrationForm, 0);
        }, { once: true });
    }

    const revealElements = document.querySelectorAll(".reveal");
    if (revealElements.length > 0) {
        const revealObserver = new IntersectionObserver(
            (entries, observer) => {
                entries.forEach((entry) => {
                    if (!entry.isIntersecting) {
                        return;
                    }
                    entry.target.classList.add("visible");
                    observer.unobserve(entry.target);
                });
            },
            {
                threshold: 0.12,
                rootMargin: "0px 0px -40px 0px",
            }
        );

        revealElements.forEach((element) => revealObserver.observe(element));
    }

    const parallaxElements = Array.from(document.querySelectorAll("[data-parallax]"));
    if (parallaxElements.length > 0) {
        const updateParallax = () => {
            const offset = window.scrollY;
            parallaxElements.forEach((element) => {
                const speed = Number(element.getAttribute("data-speed") || 0.12);
                element.style.transform = `translate3d(0, ${offset * speed}px, 0)`;
            });
        };

        updateParallax();
        window.addEventListener("scroll", updateParallax, { passive: true });
    }

    const backToTopBtn = document.getElementById("backToTopBtn");
    const aiSupportWrapper = document.getElementById("aiSupportWrapper");
    const aiSupportBtn = document.getElementById("aiSupportBtn");
    const aiSupportMinBtn = document.getElementById("aiSupportMinBtn");
    const aiSupportMaxBtn = document.getElementById("aiSupportMaxBtn");
    const aiSupportCloseBtn = document.getElementById("aiSupportCloseBtn");
    const aiWinMinBtn = document.getElementById("aiWinMinBtn");
    const aiWinRestoreBtn = document.getElementById("aiWinRestoreBtn");
    const aiWinCloseBtn = document.getElementById("aiWinCloseBtn");
    const aiSupportReopen = document.getElementById("aiSupportReopen");
    const siteFooter = document.querySelector(".site-footer");

    // Dynamic collision avoider: lifts floating buttons smoothly so they never cover footer elements
    const updateFloatingPositions = () => {
        let liftAmount = 0;
        if (siteFooter) {
            const footerRect = siteFooter.getBoundingClientRect();
            const viewportHeight = window.innerHeight;
            const overlap = viewportHeight - footerRect.top;
            if (overlap > 0) {
                liftAmount = overlap + 14;
            }
        }

        const transformValue = liftAmount > 0 ? `translateY(-${liftAmount}px)` : "";
        if (aiSupportWrapper && !aiSupportWrapper.classList.contains("is-closed")) {
            aiSupportWrapper.style.transform = transformValue;
        }
        if (aiSupportReopen && aiSupportReopen.classList.contains("is-visible")) {
            aiSupportReopen.style.transform = transformValue;
        }
        if (backToTopBtn && backToTopBtn.classList.contains("visible")) {
            backToTopBtn.style.transform = transformValue;
        }
    };

    if (backToTopBtn) {
        const toggleBackToTop = () => {
            if (window.scrollY > 400) {
                backToTopBtn.classList.add("visible");
            } else {
                backToTopBtn.classList.remove("visible");
                backToTopBtn.style.transform = "";
            }
        };

        window.addEventListener("scroll", toggleBackToTop, { passive: true });
        toggleBackToTop();
    }

    if (aiSupportWrapper) {
        const setAiSupportState = (state, save = true) => {
            aiSupportWrapper.classList.remove("is-minimized", "is-maximized", "is-closed");
            if (aiSupportReopen) {
                aiSupportReopen.classList.remove("is-visible");
            }

            if (state === "minimized") {
                aiSupportWrapper.classList.add("is-minimized");
            } else if (state === "maximized") {
                aiSupportWrapper.classList.add("is-maximized");
            } else if (state === "closed") {
                aiSupportWrapper.classList.add("is-closed");
                if (aiSupportReopen) {
                    aiSupportReopen.classList.add("is-visible");
                }
            }

            if (save) {
                sessionStorage.setItem("tektutors_ai_support_state", state);
                sessionStorage.setItem("tektutors_ai_support_minimized", String(state === "minimized"));
            }

            updateFloatingPositions();
        };

        // Initialize state from session storage
        const savedState = sessionStorage.getItem("tektutors_ai_support_state");
        const legacyMin = sessionStorage.getItem("tektutors_ai_support_minimized") === "true";
        if (savedState) {
            setAiSupportState(savedState, false);
        } else if (legacyMin) {
            setAiSupportState("minimized", false);
        }

        // Pill controls
        if (aiSupportMinBtn) {
            aiSupportMinBtn.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                setAiSupportState("minimized");
            });
        }

        if (aiSupportMaxBtn) {
            aiSupportMaxBtn.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                setAiSupportState("maximized");
            });
        }

        if (aiSupportCloseBtn) {
            aiSupportCloseBtn.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                setAiSupportState("closed");
            });
        }

        // Expanded window controls
        if (aiWinMinBtn) {
            aiWinMinBtn.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                setAiSupportState("minimized");
            });
        }

        if (aiWinRestoreBtn) {
            aiWinRestoreBtn.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                setAiSupportState("normal");
            });
        }

        if (aiWinCloseBtn) {
            aiWinCloseBtn.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                setAiSupportState("closed");
            });
        }

        // Reopen trigger button
        if (aiSupportReopen) {
            aiSupportReopen.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                setAiSupportState("normal");
            });
        }

        // Clicking the WhatsApp button while in minimized state restores normal pill view first
        if (aiSupportBtn) {
            aiSupportBtn.addEventListener("click", (e) => {
                if (aiSupportWrapper.classList.contains("is-minimized")) {
                    e.preventDefault();
                    e.stopPropagation();
                    setAiSupportState("normal");
                }
            });
        }

        // Pressing Escape closes maximized window
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && aiSupportWrapper.classList.contains("is-maximized")) {
                setAiSupportState("normal");
            }
        });

        // Active scroll dimmer: makes AI support translucent while scrolling so text beneath is visible
        let scrollTimer = null;
        window.addEventListener("scroll", () => {
            updateFloatingPositions();
            if (!aiSupportWrapper.classList.contains("is-maximized")) {
                aiSupportWrapper.classList.add("is-scrolling");
                clearTimeout(scrollTimer);
                scrollTimer = setTimeout(() => {
                    aiSupportWrapper.classList.remove("is-scrolling");
                }, 350);
            }
        }, { passive: true });

        window.addEventListener("resize", updateFloatingPositions, { passive: true });
        updateFloatingPositions();
    }

    // Registration Guide controls (Minimize, Maximize, Close, Restore)
    const registrationGuide = document.getElementById("registrationGuide");
    const registrationLayout = document.querySelector(".registration-layout");
    const regGuideMinBtn = document.getElementById("regGuideMinBtn");
    const regGuideMaxBtn = document.getElementById("regGuideMaxBtn");
    const regGuideCloseBtn = document.getElementById("regGuideCloseBtn");
    const regGuideRestoreBtn = document.getElementById("regGuideRestoreBtn");

    if (registrationGuide) {
        if (regGuideMinBtn) {
            regGuideMinBtn.addEventListener("click", (e) => {
                e.preventDefault();
                registrationGuide.classList.remove("is-maximized");
                const isMin = registrationGuide.classList.toggle("is-minimized");
                regGuideMinBtn.setAttribute("title", isMin ? "Expand guide" : "Minimize guide");
            });
        }

        if (regGuideMaxBtn) {
            regGuideMaxBtn.addEventListener("click", (e) => {
                e.preventDefault();
                registrationGuide.classList.remove("is-minimized");
                const isMax = registrationGuide.classList.toggle("is-maximized");
                regGuideMaxBtn.setAttribute("title", isMax ? "Restore standard guide view" : "Maximize / Expand guide");
            });
        }

        if (regGuideCloseBtn) {
            regGuideCloseBtn.addEventListener("click", (e) => {
                e.preventDefault();
                registrationGuide.classList.add("is-closed");
                if (registrationLayout) {
                    registrationLayout.classList.add("has-closed-guide");
                }
                if (regGuideRestoreBtn) {
                    regGuideRestoreBtn.style.display = "inline-flex";
                }
            });
        }

        if (regGuideRestoreBtn) {
            regGuideRestoreBtn.addEventListener("click", (e) => {
                e.preventDefault();
                registrationGuide.classList.remove("is-closed", "is-minimized", "is-maximized");
                if (registrationLayout) {
                    registrationLayout.classList.remove("has-closed-guide");
                }
                regGuideRestoreBtn.style.display = "none";
            });
        }
    }

    const activatePortalTab = (trigger, targetId, shouldScroll = true) => {
        if (!targetId) {
            return false;
        }

        const target = document.querySelector(targetId);
        if (!target) {
            return false;
        }

        let tabScope = trigger.closest("[data-portal-tabs]");
        if (!tabScope || !tabScope.contains(target)) {
            tabScope = document;
        }
        if (target.classList.contains("portal-tab-panel")) {
            tabScope.querySelectorAll(".portal-tab-panel").forEach((panel) => {
                panel.classList.toggle("active", panel === target);
            });
            target.classList.add("visible");
            target.querySelectorAll(".reveal").forEach((element) => element.classList.add("visible"));

            tabScope.querySelectorAll("[data-tab-target]").forEach((button) => {
                const isActive = button.getAttribute("data-tab-target") === targetId;
                button.classList.toggle("active", isActive);
                button.setAttribute("aria-selected", String(isActive));
            });
        }

        if (shouldScroll) {
            const toolbar = tabScope.querySelector?.(".portal-toolbar") || target;
            toolbar.scrollIntoView({ behavior: "smooth", block: "start" });
        }

        return true;
    };

    document.querySelectorAll("[data-jump-select]").forEach((select) => {
        select.addEventListener("change", (event) => {
            const targetId = event.target.value;
            if (activatePortalTab(event.target, targetId)) {
                event.target.value = "";
            }
        });
    });

    document.querySelectorAll("[data-tab-target]").forEach((button) => {
        button.addEventListener("click", (event) => {
            activatePortalTab(event.currentTarget, event.currentTarget.getAttribute("data-tab-target"), false);
        });
    });

    const roleFilterStorage = {
        get(key) {
            try {
                return window.sessionStorage.getItem(key);
            } catch (error) {
                return null;
            }
        },
        set(key, value) {
            try {
                window.sessionStorage.setItem(key, value);
            } catch (error) {
                // Filtering still works when browser storage is unavailable.
            }
        },
    };

    const setActiveRoleFilter = (targetId, selectedRole, shouldStore = true) => {
        document.querySelectorAll(`[data-role-filter-target="${targetId}"]`).forEach((peer) => {
            const isActive = peer.getAttribute("data-role-filter") === selectedRole;
            peer.classList.toggle("active", isActive);
            peer.setAttribute("aria-pressed", String(isActive));
        });

        if (shouldStore) {
            roleFilterStorage.set(`role-filter:${targetId}`, selectedRole);
        }
    };

    const getFilterInputForTarget = (targetId) => {
        return Array.from(document.querySelectorAll("[data-filter-target], [data-admin-filter], [data-workspace-filter]")).find((input) => {
            return (
                input.getAttribute("data-filter-target") === targetId ||
                input.getAttribute("data-admin-filter") === targetId ||
                input.getAttribute("data-workspace-filter") === targetId
            );
        });
    };

    const applyCardFilters = (targetId) => {
        const target = document.querySelector(targetId);
        if (!target) {
            return;
        }

        const searchInput = getFilterInputForTarget(targetId);
        const query = searchInput ? searchInput.value.trim().toLowerCase() : "";
        const activeRoleButton = document.querySelector(`[data-role-filter-target="${targetId}"].active`);
        const activeRole = activeRoleButton ? activeRoleButton.getAttribute("data-role-filter") : "all";
        let visibleCount = 0;

        target.querySelectorAll("[data-filter-card]").forEach((card) => {
            const matchesQuery = !query || card.textContent.toLowerCase().includes(query);
            const cardRole = card.getAttribute("data-user-role");
            const matchesRole = !cardRole || activeRole === "all" || cardRole === activeRole;
            const shouldShow = matchesQuery && matchesRole;
            card.hidden = !shouldShow;
            if (shouldShow) {
                visibleCount += 1;
            }
        });

        document.querySelectorAll(`[data-filter-empty="${targetId}"]`).forEach((emptyState) => {
            emptyState.hidden = visibleCount > 0;
        });
    };

    document.querySelectorAll("[data-filter-target], [data-admin-filter], [data-workspace-filter]").forEach((input) => {
        const handleFilterInput = (event) => {
            const targetId =
                event.target.getAttribute("data-filter-target") ||
                event.target.getAttribute("data-admin-filter") ||
                event.target.getAttribute("data-workspace-filter");
            applyCardFilters(targetId);
        };

        input.addEventListener("input", handleFilterInput);
        input.addEventListener("search", handleFilterInput);
        input.addEventListener("change", handleFilterInput);
        input.addEventListener("keyup", handleFilterInput);
    });

    document.querySelectorAll("[data-role-filter-target]").forEach((button) => {
        const targetId = button.getAttribute("data-role-filter-target");
        const savedRole = roleFilterStorage.get(`role-filter:${targetId}`);

        if (savedRole) {
            setActiveRoleFilter(targetId, savedRole, false);
        }
    });

    document.addEventListener("click", (event) => {
        const currentButton = event.target.closest("[data-role-filter-target]");
        if (!currentButton) {
            return;
        }

        const targetId = currentButton.getAttribute("data-role-filter-target");
        const selectedRole = currentButton.getAttribute("data-role-filter");
        const activateTargetId = currentButton.getAttribute("data-role-filter-activate");
        if (activateTargetId) {
            activatePortalTab(currentButton, activateTargetId, false);
        }
        setActiveRoleFilter(targetId, selectedRole);
        applyCardFilters(targetId);
    });

    Array.from(new Set(Array.from(document.querySelectorAll("[data-role-filter-target]")).map((button) => button.getAttribute("data-role-filter-target")))).forEach(applyCardFilters);

    document.querySelectorAll("[data-confirm]").forEach((element) => {
        element.addEventListener("click", (event) => {
            const message = element.getAttribute("data-confirm");
            if (message && !window.confirm(message)) {
                event.preventDefault();
            }
        });
    });
});
