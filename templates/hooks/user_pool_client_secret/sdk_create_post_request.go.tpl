if err == nil && resp.ClientSecretDescriptor == nil {
	return nil, ackerr.NewTerminalError(fmt.Errorf("Cognito did not return a client secret descriptor"))
}
