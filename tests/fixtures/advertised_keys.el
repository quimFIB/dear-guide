;; Every C-c key a buffer's own header advertises must be bound to something
;; that does not error in that buffer.  One assertion, and the class cannot
;; come back: a new buffer kind either offers the keys or stops naming them.
(let ((file (car command-line-args-left)))
  (find-file file)
  (dolist (key (dgraph--advertised-keys))
    (let ((fn (key-binding (kbd key))))
      (unless (commandp fn)
        (error "%s is advertised and bound to nothing" key))
      ;; C-c C-c and C-c C-k finish the session; running them here would exit.
      ;; The walk keys are the ones this is about.
      (when (member key '("C-c C-p" "C-c C-a"))
        (condition-case e (funcall fn)
          (error (error "%s errors: %s" key (error-message-string e)))))))
  (princ (format "ok %s: %s\n" (dgraph--op)
                 (string-join (dgraph--advertised-keys) " "))))
