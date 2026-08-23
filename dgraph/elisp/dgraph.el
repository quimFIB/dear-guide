;;; dgraph.el --- compose a decision-graph entry in org -*- lexical-binding: t; -*-

;; Loaded by `dg` itself (`emacs -l .../dgraph.el .dgraph-edit.org`), so there is
;; nothing to install and nothing to add to your init file.

;;; Commentary:

;; `dg decide --edit` writes an org buffer, launches emacs on it, and blocks.
;; This file adds the affordances that make that buffer pleasant: a `dg:' link
;; type so C-c C-o walks the graph, a read-only Context subtree, and C-c C-c to
;; finish the way `git commit' does.
;;
;; Two invariants, both deliberate:
;;
;; - This file NEVER modifies the graph.  Every subprocess call goes through
;;   `dgraph--dg', which refuses any subcommand not in
;;   `dgraph-readonly-commands'.  Staging is the CLI's job, after emacs exits.
;;   tests/test_cli.py greps this file to keep that honest.
;;
;; - Nothing here is load-bearing for correctness.  The read-only guard and the
;;   pre-flight check below are conveniences; `dgraph/editor.py' re-validates
;;   everything, because a user in vim bypasses all of it.

;;; Code:

(require 'org)
(require 'json)
(require 'subr-x)

(defgroup dgraph nil
  "Compose decision-graph entries in org."
  :group 'tools)

(defconst dgraph-readonly-commands '("export")
  "The only `dg' subcommands this file may run.  None of them write.")

(defcustom dgraph-program "dg"
  "The `dg' executable."
  :type 'string :group 'dgraph)

(defvar-local dgraph--cache nil
  "Parsed `dg export' output for this buffer.")

;;;; Talking to dg -----------------------------------------------------------

(defun dgraph--project ()
  "The project root, from the buffer's properties drawer or the environment."
  (or (save-excursion
        (goto-char (point-min))
        (and (re-search-forward "^:DGRAPH_PROJECT:[ \t]*\\(.*\\)$" nil t)
             (string-trim (match-string 1))))
      (getenv "DG_PROJECT")
      default-directory))

(defun dgraph--dg (&rest args)
  "Run `dg' with ARGS and return stdout.  Read-only subcommands only."
  (unless (member (car args) dgraph-readonly-commands)
    (error "dgraph.el refuses to run `dg %s' — read-only commands only, %s"
           (car args) "staging is the CLI's job"))
  (with-temp-buffer
    (let ((status (apply #'call-process dgraph-program nil t nil
                         (append (list "--project" (dgraph--project)) args))))
      (unless (zerop status)
        (error "dg %s failed: %s" (string-join args " ") (buffer-string)))
      (buffer-string))))

(defun dgraph--graph ()
  "The graph as an alist, cached per buffer."
  (or dgraph--cache
      (setq dgraph--cache
            (let ((json-object-type 'alist) (json-array-type 'list))
              (json-read-from-string (dgraph--dg "export"))))))

(defun dgraph--vertex (id)
  (seq-find (lambda (v) (equal (alist-get 'id v) id))
            (alist-get 'vertices (dgraph--graph))))

(defun dgraph--derived (id)
  (alist-get (intern id) (alist-get 'derived (dgraph--graph))))

(defun dgraph--edge (id)
  (seq-find (lambda (e) (and (equal (alist-get 'from e) id)
                             (not (eq (alist-get 'active e) :json-false))))
            (alist-get 'edges (dgraph--graph))))

;;;; Showing a node ----------------------------------------------------------

(defun dgraph--show (id)
  "Pop up a read-only view of decision ID."
  (let* ((v (dgraph--vertex id))
         (d (dgraph--derived id))
         (e (dgraph--edge id)))
    (unless v (error "No such decision: %s" id))
    (with-current-buffer (get-buffer-create (format "*dgraph: %s*" id))
      (let ((inhibit-read-only t))
        (erase-buffer)
        (org-mode)
        (insert (format "* %s — %s\n" id (alist-get 'title v)))
        (insert (format "  status %s · area %s · depth %s\n"
                        (alist-get 'status v) (alist-get 'area v)
                        (alist-get 'depth d)))
        (insert (format "  depends on %s\n"
                        (or (string-join (alist-get 'depends d) ", ") "—")))
        (insert (format "  opens      %s\n"
                        (or (string-join (alist-get 'children d) ", ") "—")))
        (when-let ((f (and e (alist-get 'falsifier e))))
          (insert (format "  falsifier  %s\n" f)))
        (when-let ((s (and e (alist-get 'source e))))
          (insert (format "  source     %s\n" s)))
        (when-let ((a (and e (alist-get 'answer e))))
          (insert "\n" a "\n"))
        (when-let ((n (alist-get 'note v)))
          (insert "\n" n "\n"))
        (dolist (h (alist-get 'history d))
          (insert (format "\n superseded: “%s” → %s\n   %s\n"
                          (alist-get 'summary h)
                          (or (alist-get 'replaced_by h) "(undecided)")
                          (or (alist-get 'why h) ""))))
        (goto-char (point-min)))
      (special-mode)
      (local-set-key "q" #'quit-window)
      (display-buffer (current-buffer)))))

;;;; The dg: link type -------------------------------------------------------

;; Registering a link type is what makes C-c C-o work: it is plain
;; `org-open-at-point', so there is no binding to shadow and `file:' links in
;; the Source field become clickable for free.
(org-link-set-parameters
 "dg"
 :follow (lambda (path _) (dgraph--show (string-trim path)))
 :face 'org-link)

;;;; Navigation --------------------------------------------------------------

(defun dgraph-visit (id)
  "Show decision ID, prompting with the graph's ids."
  (interactive
   (list (completing-read
          "Decision: "
          (mapcar (lambda (v) (alist-get 'id v))
                  (alist-get 'vertices (dgraph--graph))))))
  (dgraph--show id))

(defun dgraph-parent ()
  "Show a premise of the decision being composed."
  (interactive)
  (let* ((id (dgraph--target))
         (deps (alist-get 'depends (dgraph--derived id))))
    (pcase (length deps)
      (0 (message "%s rests on nothing — it is a root" id))
      (1 (dgraph--show (car deps)))
      (_ (dgraph--show (completing-read "Premise: " deps nil t))))))

(defun dgraph-ancestors ()
  "List every premise this decision transitively rests on."
  (interactive)
  (let ((seen '()) (queue (list (dgraph--target))))
    (while queue
      (let ((cur (pop queue)))
        (dolist (p (alist-get 'depends (dgraph--derived cur)))
          (unless (member p seen) (push p seen) (push p queue)))))
    (if (null seen)
        (message "No ancestors — this is a root decision")
      (message "Rests on: %s" (string-join (sort seen #'string<) " → ")))))

(defun dgraph--walkable ()
  "The decision this buffer is composing, or nil if there is nothing to walk.

Four buffer kinds share this file name, and only two of them name a vertex
that exists: `dg decide' and `dg reopen'.  `dg add' is composing one that does
not exist yet, and both task buffers belong to a store this file cannot read at
all.  Returning nil rather than signalling lets the mode decline to *bind* the
walk keys, so the header and the keymap agree — advertising a key that errors
is what interface audit F8 was about."
  (save-excursion
    (goto-char (point-min))
    (and (re-search-forward "^:DGRAPH_VERTEX:[ \t]*\\(D[0-9]+\\)" nil t)
         (match-string 1))))

(defun dgraph--target ()
  "The decision this buffer is composing.  Errors where there is none.

The message names the buffer's own op, because \"not composing a decision\" was
false in a `dg add' buffer — which is composing one — and unhelpful in a task
buffer, which is composing something this file has no way to show."
  (or (dgraph--walkable)
      (error "%s: this buffer has no premises to walk"
             (or (dgraph--op) "this buffer"))))

(defun dgraph--op ()
  "The op this buffer composes, from its properties drawer."
  (save-excursion
    (goto-char (point-min))
    (and (re-search-forward "^:DGRAPH_OP:[ \t]*\\(.*\\)$" nil t)
         (string-trim (match-string 1)))))

;;;; Finishing ---------------------------------------------------------------

(defun dgraph--input-bounds ()
  "Bounds of the `* Input' subtree, or nil."
  (save-excursion
    (goto-char (point-min))
    (when (re-search-forward "^\\* Input[ \t]*$" nil t)
      (let ((start (point)))
        (cons start (if (re-search-forward "^\\* " nil t)
                        (match-beginning 0)
                      (point-max)))))))

(defun dgraph--check ()
  "Return a complaint string if the buffer is not ready, else nil.

Catching this here beats a CLI error after the buffer is gone.  `editor.parse'
checks the same things again — a user in another editor never runs this."
  (let ((bounds (dgraph--input-bounds)))
    (cond
     ((null bounds) "no `* Input' section — is this a dg buffer?")
     (t
      (save-excursion
        (goto-char (car bounds))
        (let (empty)
          (while (re-search-forward "^\\*\\* \\(.+?\\)[ \t]*$" (cdr bounds) t)
            (let* ((name (match-string 1))
                   (from (point))
                   (to (or (save-excursion
                             (and (re-search-forward "^\\*\\{1,2\\} " (cdr bounds) t)
                                  (match-beginning 0)))
                           (cdr bounds)))
                   (body (replace-regexp-in-string
                          "^[ \t]*#\\(?:[^+].*\\)?$" ""
                          (buffer-substring-no-properties from to))))
              (when (and (member name '("Answer" "Source" "Why" "Title" "Area" "Id"))
                         (string-empty-p (string-trim body)))
                (push name empty))))
          (when empty
            (format "%s still empty" (string-join (nreverse empty) ", ")))))))))

(defun dgraph-finish ()
  "Save and hand the buffer back to `dg'."
  (interactive)
  (when-let ((complaint (dgraph--check)))
    (goto-char (car (dgraph--input-bounds)))
    (user-error "Not finishing: %s (C-c C-k to abort instead)" complaint))
  (save-buffer)
  (kill-emacs 0))

(defun dgraph-abort ()
  "Discard the buffer.  `dg' sees it empty and stages nothing."
  (interactive)
  (when (yes-or-no-p "Discard this decision and stage nothing? ")
    (let ((inhibit-read-only t))
      (erase-buffer))
    (save-buffer)
    ;; exit 0 deliberately: a nonzero status means the editor failed, which is a
    ;; different thing from the user changing their mind.
    (kill-emacs 0)))

;;;; The mode ----------------------------------------------------------------

(defvar dgraph-edit-mode-map
  (let ((m (make-sparse-keymap)))
    ;; C-c C-c shadows `org-ctrl-c-ctrl-c' in this buffer only, matching what
    ;; git-commit-mode does. C-c C-o is left alone: the dg: link type means
    ;; org-open-at-point already does the right thing.
    ;;
    ;; These two are the only `C-c C-<letter>' keys this mode takes, and both
    ;; are the price of reading as a commit buffer: somebody who has finished
    ;; typing reaches for C-c C-c without looking. Everything else lives under
    ;; `dgraph-prefix'; see there for why.
    ;;
    ;; The walk keys are NOT here.  They only work in a buffer naming a vertex
    ;; that exists, so they are bound per buffer in `dgraph-edit-mode' below —
    ;; a binding present everywhere and working in half the buffers is the
    ;; shape F8 reported.
    (define-key m (kbd "C-c C-c") #'dgraph-finish)
    (define-key m (kbd "C-c C-k") #'dgraph-abort)
    m)
  "Keymap for `dgraph-edit-mode'.")

(defconst dgraph-prefix "C-c d"
  "The prefix the navigation keys live under.

`C-c <letter>' is space Emacs reserves for the user, and a package taking one
is a small trespass.  It is the lesser of the two available: this buffer is an
*org* buffer, and the alternative — staying in the major mode's `C-c
C-<letter>' namespace — means shadowing org itself.  Three keys did, and one
of them was `C-c C-v', which in org is not a command but the whole
`org-babel' prefix map: forty-odd commands traded for one, in a buffer whose
Context subtree can hold source blocks.  `C-c C-p' and `C-c C-a' cost
`org-previous-visible-heading' and `org-attach' on top of that.

So the navigation keys moved here and org keeps all three.  What did not move
is C-c C-c and C-c C-k, which are worth their shadow and say so above.")

(defvar dgraph-walk-map
  (let ((m (make-sparse-keymap)))
    (define-key m (kbd "p") #'dgraph-parent)
    (define-key m (kbd "a") #'dgraph-ancestors)
    (define-key m (kbd "v") #'dgraph-visit)
    m)
  "Navigation keys, under `dgraph-prefix'.

Bound per buffer rather than in `dgraph-edit-mode-map', because all three read
`:DGRAPH_VERTEX:' and a buffer composing a decision that does not exist yet
has none to read.")

(defun dgraph--protect-context ()
  "Make the `* Context' subtree read-only.

Uses the text property, not an overlay: `read-only' as an overlay property is
silently ignored by Emacs, so an overlay here would look right and protect
nothing."
  (save-excursion
    (goto-char (point-min))
    (when (re-search-forward "^\\* Context" nil t)
      (let ((inhibit-read-only t)
            (start (match-beginning 0)))
        (put-text-property start (point-max) 'read-only t)
        ;; so typing at the very end of the last field is not captured
        (put-text-property start (min (1+ start) (point-max)) 'front-sticky
                           '(read-only))))))

(define-minor-mode dgraph-edit-mode
  "Minor mode for a `dg' editor buffer."
  :lighter " dgraph" :keymap dgraph-edit-mode-map
  (when dgraph-edit-mode
    ;; Bound only where they can work, and where the header says so.  The two
    ;; have to agree: the header is the only documentation these keys have,
    ;; and `dgraph--every-bound-key-is-advertised' checks it in both
    ;; directions — `C-c C-v' was bound here for a long time and named in no
    ;; header, which is the half that used to be unwatched.
    (when (dgraph--walkable)
      (local-set-key (kbd dgraph-prefix) dgraph-walk-map))
    (setq-local org-startup-folded nil)
    (dgraph--protect-context)
    (org-fold-hide-sublevels 1)
    ;; land on the first field, ready to type
    (goto-char (point-min))
    (when (re-search-forward "^\\*\\* " nil t)
      (org-fold-show-subtree)
      (forward-line 1)
      (while (looking-at "^[ \t]*#") (forward-line 1)))
    (message "C-c C-c to stage · C-c C-k to abort · C-c C-o follows a dg: link")))

(defun dgraph--advertised-keys ()
  "The C-c bindings this buffer's own header claims.  Read back for the test.

Matches both shapes: the two `C-c C-<letter>' keys this mode keeps, and the
navigation keys under `dgraph-prefix'."
  (save-excursion
    (goto-char (point-min))
    (let (found)
      (while (re-search-forward "C-c \\(?:C-[a-z]\\|d [a-z]\\)" nil t)
        (push (match-string 0) found))
      (delete-dups (nreverse found)))))

(defun dgraph--bound-keys ()
  "Every key in this buffer bound to a command this package defines.

The other half of `dgraph--advertised-keys'.  Deliberately scoped to
`dgraph-' commands rather than to every binding: an org buffer has some two
dozen `C-c C-<letter>' keys and all but ours are org's own, so \"everything
bound is advertised\" would be false by design.  What must hold is that
nothing *this package* binds is undocumented."
  (let (found)
    (mapatoms
     (lambda (sym)
       (when (and (commandp sym)
                  (string-prefix-p "dgraph-" (symbol-name sym))
                  ;; `dgraph--' is private; only user-facing commands are
                  ;; things a header could sensibly name.
                  (not (string-prefix-p "dgraph--" (symbol-name sym))))
         ;; Over every active keymap, not `current-local-map': the walk keys
         ;; are local but C-c C-c and C-c C-k live in the minor-mode map, and
         ;; a check that could not see those would have a blind spot of
         ;; exactly the kind it exists to close.
         (dolist (k (where-is-internal sym))
           (push (key-description k) found)))))
    (delete-dups (nreverse found))))

(defun dgraph--every-bound-key-is-advertised ()
  "Keys this package binds here that no header names.  Nil when all is well.

The direction that was missing.  `advertised_keys.el' walked the header and
checked each key was bound, so a key advertised and broken failed — and a key
bound and undocumented could not.  `C-c C-v' sat in that gap from the commit
that introduced the editor until somebody read the two lists side by side."
  (let ((named (dgraph--advertised-keys)))
    (seq-remove (lambda (k) (member k named)) (dgraph--bound-keys))))

(defun dgraph--maybe-enable ()
  (when (and buffer-file-name
             (string-match-p "\\.dgraph-edit\\.org\\'" buffer-file-name))
    (dgraph-edit-mode 1)))

(add-to-list 'auto-mode-alist '("\\.dgraph-edit\\.org\\'" . org-mode))
(add-hook 'org-mode-hook #'dgraph--maybe-enable)

;;;; Self-test ---------------------------------------------------------------

(defun dgraph--selftest ()
  "Assert the file loaded coherently.  Run by the test suite."
  (dolist (sym '(dgraph-finish dgraph-abort dgraph-parent dgraph-ancestors
                 dgraph-visit dgraph-edit-mode dgraph--walkable dgraph--op
                 dgraph--advertised-keys))
    (unless (fboundp sym) (error "missing: %s" sym)))
  (unless (org-link-get-parameter "dg" :follow)
    (error "the dg: link type did not register"))
  (unless (equal dgraph-readonly-commands '("export"))
    (error "unexpected command allowlist"))
  (princ "dgraph.el selftest ok\n"))

(provide 'dgraph)
;;; dgraph.el ends here
