// Package svc serves the jobs API.
package svc

import (
	"fmt"
	"net/http"
)

// MaxBody caps request bodies.
const MaxBody = 1 << 20

const (
	// ModeFast skips checks.
	ModeFast = "fast"
	modeSlow = "slow"
)

var DefaultName = "svc"

// Server is the HTTP front end.
type Server struct {
	Addr    string `json:"addr"`
	Timeout int
	mux     *http.ServeMux
	*Logger
}

// Handler handles one route.
type Handler interface {
	Serve(w http.ResponseWriter, r *http.Request) error
	name() string
}

type ID = string

// Start listens on Addr.
func (s *Server) Start(ctx Context, port int) (err error) {
	fmt.Println("{")
	return nil
}

func (s *Server) stop() {}

// New returns a Server.
func New(addr string) *Server {
	return &Server{Addr: addr}
}

func internal() {}
