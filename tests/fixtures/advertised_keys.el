;; The header and the bindings, checked against each other in BOTH
;; directions.  A new buffer kind either offers the keys or stops naming them;
;; it cannot do neither, and it cannot do one without the other.
;;
;; Only the first direction existed at first, and the gap was not theoretical:
;; `C-c C-v' was bound to `dgraph-visit' in every walkable buffer, named in no
;; header, from the commit that introduced the editor onwards.  A key nothing
;; documents is a key nobody presses — and that one also shadowed the whole
;; `org-babel' prefix map to do it.
(let ((file (car command-line-args-left)))
  (find-file file)

  ;; 1. advertised -> bound.  Nothing is named that does not work here.
  (dolist (key (dgraph--advertised-keys))
    (let ((fn (key-binding (kbd key))))
      (unless (commandp fn)
        (error "%s is advertised and bound to nothing" key))
      ;; C-c C-c and C-c C-k finish the session; running them here would exit.
      ;; `C-c d v' is left out for a different reason: `dgraph-visit' takes an
      ;; id, and its value comes from an `interactive' spec that prompts —
      ;; `funcall' with no argument tests the wrong thing and
      ;; `call-interactively' would block.  What it does once it has an id is
      ;; `dgraph--show', which p and a both run below.
      (when (member key '("C-c d p" "C-c d a"))
        (condition-case e (funcall fn)
          (error (error "%s errors: %s" key (error-message-string e)))))))

  ;; 2. bound -> advertised.  Nothing this package binds is undocumented.
  (let ((silent (dgraph--every-bound-key-is-advertised)))
    (when silent
      (error "bound to a dgraph command and named in no header: %s"
             (string-join silent " "))))

  (princ (format "ok %s: %s\n" (dgraph--op)
                 (string-join (dgraph--advertised-keys) " "))))
